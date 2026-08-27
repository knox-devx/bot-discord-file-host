from __future__ import annotations

import asyncio
import io
import logging
import tempfile
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from bot.api_client import FileHostClient, FileHostError, MAX_SIGNED_URL_SECONDS
from bot.config import Settings

logger = logging.getLogger(__name__)

DISCORD_BUTTON_URL_MAX = 512
DISCORD_MESSAGE_CONTENT_MAX = 2000


def format_bytes(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def format_duration(seconds: int) -> str:
    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days} dia" if days == 1 else f"{days} dias"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hora" if hours == 1 else f"{hours} horas"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} minuto" if minutes == 1 else f"{minutes} minutos"
    return f"{seconds} segundos"


def safe_filename(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "._- ").strip()
    return cleaned[:180] or "arquivo.bin"


def can_use_button_url(url: str | None) -> bool:
    return bool(url) and len(url) <= DISCORD_BUTTON_URL_MAX


def make_link_attachment(url: str) -> discord.File | None:
    if len(url) <= DISCORD_MESSAGE_CONTENT_MAX:
        return None
    return discord.File(io.BytesIO(url.encode("utf-8")), filename="link.txt")


def link_message_content(url: str) -> str:
    if len(url) <= DISCORD_MESSAGE_CONTENT_MAX:
        return url
    return "🔗 O link completo está no arquivo `link.txt` anexado a esta mensagem."


async def _send_full_link_to_interaction(interaction: discord.Interaction, url: str) -> None:
    kwargs: dict[str, object] = {
        "content": link_message_content(url),
        "ephemeral": interaction.guild is not None,
    }
    attachment = make_link_attachment(url)
    if attachment is not None:
        kwargs["file"] = attachment
    await interaction.response.send_message(**kwargs)  # type: ignore[arg-type]


async def _send_full_link_dm(user: discord.abc.User, url: str) -> bool:
    kwargs: dict[str, object] = {"content": link_message_content(url)}
    attachment = make_link_attachment(url)
    if attachment is not None:
        kwargs["file"] = attachment
    try:
        await user.send(**kwargs)  # type: ignore[arg-type]
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


class RevealLinkButton(discord.ui.Button):
    def __init__(self, *, owner_id: int, full_url: str) -> None:
        super().__init__(
            label="Receber link",
            emoji="📩",
            style=discord.ButtonStyle.primary,
            custom_id=f"knox-reveal-link:{owner_id}",
        )
        self.owner_id = owner_id
        self.full_url = full_url

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Este botão pertence à pessoa que fez o upload.",
                ephemeral=True,
            )
            return

        await _send_full_link_to_interaction(interaction, self.full_url)

        if interaction.guild is not None:
            await _send_full_link_dm(interaction.user, self.full_url)


class LinkView(discord.ui.View):
    def __init__(
        self,
        *,
        owner_id: int,
        full_url: str,
        button_url: str | None,
        view_url: str | None = None,
    ) -> None:
        super().__init__(timeout=3600)

        if can_use_button_url(button_url):
            self.add_item(discord.ui.Button(label="Abrir arquivo", url=button_url, emoji="🔗"))
        else:
            self.add_item(RevealLinkButton(owner_id=owner_id, full_url=full_url))

        if view_url and view_url != button_url and can_use_button_url(view_url):
            self.add_item(discord.ui.Button(label="Preview", url=view_url, emoji="👁️"))


class FileHostCog(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings, api: FileHostClient) -> None:
        self.bot = bot
        self.settings = settings
        self.api = api
        self._user_locks: dict[int, asyncio.Lock] = {}

    def _lock_for(self, user_id: int) -> asyncio.Lock:
        return self._user_locks.setdefault(user_id, asyncio.Lock())

    def _absolute_view_url(self, view_url: str | None) -> str | None:
        if not view_url:
            return None
        if view_url.startswith("https://") or view_url.startswith("http://"):
            return view_url
        if view_url.startswith("/"):
            return f"{self.settings.site_url}{view_url}"
        return f"{self.settings.site_url}/{view_url.lstrip('/')}"

    async def _resolve_button_url(
        self,
        full_url: str,
        *,
        expires_in: int | None,
    ) -> tuple[str | None, bool]:
        if can_use_button_url(full_url):
            return full_url, False

        try:
            shortened = await self.api.shorten_url(full_url, expires_in=expires_in)
            if can_use_button_url(shortened.short_url):
                return shortened.short_url, True
            logger.warning(
                "Encurtador retornou URL acima de 512 caracteres | tamanho=%s",
                len(shortened.short_url),
            )
        except FileHostError as exc:
            logger.warning("Não foi possível encurtar o link: %s", exc)

        return None, False

    async def _host_attachment(
        self,
        interaction: discord.Interaction,
        attachment: discord.Attachment,
        *,
        private: bool,
        password: str | None,
        expires_in: int,
    ) -> None:
        lock = self._lock_for(interaction.user.id)
        if lock.locked():
            await interaction.response.send_message(
                "Você já tem um upload em andamento. Termine esse envio antes de iniciar outro.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        async with lock:
            temp_path: Path | None = None
            try:
                suffix = Path(safe_filename(attachment.filename)).suffix
                with tempfile.NamedTemporaryFile(prefix="knox-host-", suffix=suffix, delete=False) as temp:
                    temp_path = Path(temp.name)

                await attachment.save(temp_path, use_cached=True)

                upload = await self.api.upload(
                    file_path=temp_path,
                    original_name=attachment.filename,
                    content_type=attachment.content_type,
                    private=private,
                    password=password,
                )

                signed = None
                final_url = upload.file_url

                if private:
                    if not upload.file_uri:
                        raise FileHostError(
                            "A API marcou o arquivo como privado, mas não retornou file_uri "
                            "para criar o link temporário."
                        )
                    signed = await self.api.create_signed_url(
                        upload.file_uri,
                        expires_in=expires_in,
                        password=password,
                    )
                    final_url = signed.signed_url

                if not final_url:
                    raise FileHostError("A API não retornou um link utilizável para este arquivo.")

                preview_url = self._absolute_view_url(upload.view_url)
                button_url, shortened = await self._resolve_button_url(
                    final_url,
                    expires_in=expires_in if signed else None,
                )

                embed = discord.Embed(
                    title="✅ Arquivo hospedado",
                    description=f"Seu arquivo foi enviado com sucesso pelo **{self.settings.bot_name}**.",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Arquivo", value=f"`{upload.name[:100]}`", inline=False)
                embed.add_field(
                    name="Tamanho",
                    value=format_bytes(upload.size if upload.size is not None else attachment.size),
                    inline=True,
                )
                embed.add_field(
                    name="Tipo",
                    value=f"`{upload.mime_type or attachment.content_type or 'desconhecido'}`",
                    inline=True,
                )
                embed.add_field(
                    name="Armazenamento",
                    value="🔒 Privado" if private else "🌐 Público",
                    inline=True,
                )

                if button_url:
                    embed.add_field(
                        name="Link temporário" if signed else "Link permanente",
                        value=button_url,
                        inline=False,
                    )
                else:
                    embed.add_field(
                        name="Link",
                        value="📩 O link é grande demais para um botão. Use **Receber link** abaixo.",
                        inline=False,
                    )

                if shortened:
                    embed.add_field(
                        name="🔗 Encurtador",
                        value="O link original excedia o limite do Discord e foi encurtado pela **Dev Cloud**.",
                        inline=False,
                    )

                if signed:
                    embed.add_field(
                        name="Expiração",
                        value=(
                            f"{format_duration(signed.expires_in or expires_in)}"
                            + (f"\n`{signed.expires_at}`" if signed.expires_at else "")
                        ),
                        inline=False,
                    )

                if password:
                    embed.add_field(
                        name="Proteção",
                        value="🔑 Arquivo enviado com senha de proteção.",
                        inline=False,
                    )

                embed.add_field(name="ID", value=f"`{upload.id}`", inline=True)
                embed.set_footer(text="Criado e mantido por Knox Dev")

                def make_view() -> LinkView:
                    return LinkView(
                        owner_id=interaction.user.id,
                        full_url=final_url,
                        button_url=button_url,
                        view_url=preview_url,
                    )

                dm_sent = interaction.guild is None
                if interaction.guild is not None:
                    try:
                        await interaction.user.send(embed=embed.copy(), view=make_view())
                        dm_sent = True
                    except (discord.Forbidden, discord.HTTPException) as exc:
                        logger.info(
                            "Não foi possível enviar DM | usuário=%s erro=%s",
                            interaction.user.id,
                            type(exc).__name__,
                        )

                local_embed = embed.copy()
                if dm_sent:
                    local_embed.add_field(
                        name="📩 DM",
                        value="Também enviei uma cópia deste resultado na sua DM.",
                        inline=False,
                    )
                else:
                    local_embed.add_field(
                        name="⚠️ DM bloqueada",
                        value=(
                            "Não consegui enviar a cópia por DM. A resposta continua disponível aqui "
                            "somente para você."
                        ),
                        inline=False,
                    )

                await interaction.followup.send(
                    embed=local_embed,
                    view=make_view(),
                    ephemeral=True,
                )

                logger.info(
                    "Upload concluído | usuário=%s arquivo=%s tamanho=%s privado=%s dm=%s encurtado=%s endpoint=%s",
                    interaction.user.id,
                    attachment.filename,
                    attachment.size,
                    private,
                    dm_sent,
                    shortened,
                    upload.endpoint,
                )
            except FileHostError as exc:
                logger.warning("Falha na API ao hospedar %s: %s", attachment.filename, exc)
                details = str(exc)
                if len(details) > 1700:
                    details = details[:1700] + "…"
                await interaction.followup.send(
                    "❌ **Não consegui hospedar o arquivo.**\n"
                    "A File Host API recusou ou não conseguiu concluir a operação.\n\n"
                    f"```text\n{details}\n```",
                    ephemeral=True,
                )
            except discord.HTTPException as exc:
                logger.exception("Erro do Discord durante upload")
                await interaction.followup.send(
                    f"❌ O Discord retornou um erro enquanto eu processava o arquivo: `{exc}`",
                    ephemeral=True,
                )
            except Exception:
                logger.exception("Erro inesperado durante hospedagem")
                await interaction.followup.send(
                    "❌ Ocorreu um erro inesperado ao processar o arquivo. Veja os logs do bot para detalhes.",
                    ephemeral=True,
                )
            finally:
                if temp_path:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except OSError:
                        logger.warning("Não foi possível remover o temporário %s", temp_path)

    @app_commands.command(name="hospedar", description="Hospeda um arquivo e envia o link aqui e na sua DM.")
    @app_commands.describe(
        arquivo="Arquivo que será enviado para a hospedagem.",
        privado="Armazena o arquivo como privado na File Host API.",
        senha="Senha opcional de proteção do arquivo.",
        expira_em="Segundos do link temporário privado: 60 até 2592000.",
    )
    async def hospedar(
        self,
        interaction: discord.Interaction,
        arquivo: discord.Attachment,
        privado: bool = False,
        senha: str | None = None,
        expira_em: app_commands.Range[int, 60, MAX_SIGNED_URL_SECONDS] = 3600,
    ) -> None:
        await self._host_attachment(
            interaction,
            arquivo,
            private=privado,
            password=senha,
            expires_in=int(expira_em),
        )

    @app_commands.command(name="sobre", description="Mostra informações sobre o serviço de hospedagem.")
    async def sobre(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title=f"📦 {self.settings.bot_name}",
            description=(
                "Hospeda arquivos pelo Discord usando a **File Host API / Dev Cloud**.\n\n"
                "• upload público e privado\n"
                "• links temporários assinados\n"
                "• senha opcional\n"
                "• encurtamento automático de links grandes\n"
                "• resultado por DM + resposta ephemeral\n\n"
                "Documentação: https://dev-cloud.base44.app/docs"
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Comando", value="`/hospedar`", inline=True)
        embed.add_field(name="Créditos", value="Knox Dev", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    settings: Settings = bot.settings  # type: ignore[attr-defined]
    api: FileHostClient = bot.file_host_api  # type: ignore[attr-defined]
    await bot.add_cog(FileHostCog(bot, settings, api))
