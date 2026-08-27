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
DISCORD_EMBED_FIELD_MAX = 1024
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
    """O Discord limita URLs de botões a 512 caracteres."""
    return bool(url) and len(url) <= DISCORD_BUTTON_URL_MAX


def embed_link_value(url: str) -> str:
    """Evita ultrapassar o limite de 1024 caracteres de um campo de embed."""
    if len(url) <= DISCORD_EMBED_FIELD_MAX:
        return url
    return "🔗 O link é muito longo para caber neste campo. Ele foi enviado junto da mensagem."


def message_link_content(url: str) -> str | None:
    """Quando a URL não cabe no embed, tenta entregá-la no conteúdo da mensagem."""
    if len(url) <= DISCORD_EMBED_FIELD_MAX:
        return None
    if len(url) <= DISCORD_MESSAGE_CONTENT_MAX:
        return url
    return "🔗 O link completo está no arquivo `link.txt` anexado a esta mensagem."


def link_text_file(url: str) -> discord.File | None:
    """Fallback extremo para URLs maiores que o limite de conteúdo do Discord."""
    if len(url) <= DISCORD_MESSAGE_CONTENT_MAX:
        return None
    return discord.File(io.BytesIO(url.encode("utf-8")), filename="link.txt")


class LinkView(discord.ui.View):
    def __init__(self, file_url: str, view_url: str | None = None) -> None:
        super().__init__(timeout=None)

        # Signed URLs podem ser maiores que 512 caracteres. Nesse caso o link
        # continua sendo entregue no embed/conteúdo, mas não pode virar botão.
        if can_use_button_url(file_url):
            self.add_item(discord.ui.Button(label="Abrir arquivo", url=file_url, emoji="🔗"))

        if view_url and view_url != file_url and can_use_button_url(view_url):
            self.add_item(discord.ui.Button(label="Preview", url=view_url, emoji="👁️"))


def make_link_view(file_url: str, view_url: str | None = None) -> LinkView | None:
    view = LinkView(file_url, view_url)
    return view if view.children else None


class FileHostCog(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings, api: FileHostClient) -> None:
        self.bot = bot
        self.settings = settings
        self.api = api
        self._user_locks: dict[int, asyncio.Lock] = {}

    def _lock_for(self, user_id: int) -> asyncio.Lock:
        return self._user_locks.setdefault(user_id, asyncio.Lock())

    def _absolute_view_url(self, view_url: str | None) -> str | None:
        """Converte caminhos como /file/abc123 em URL absoluta para os botões do Discord."""
        if not view_url:
            return None
        if view_url.startswith("https://") or view_url.startswith("http://"):
            return view_url
        if view_url.startswith("/"):
            return f"{self.settings.site_url}{view_url}"
        return f"{self.settings.site_url}/{view_url.lstrip('/')}"

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

        # A resposta no local onde o comando foi usado é sempre privada (ephemeral).
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

                if signed:
                    embed.add_field(name="Link temporário", value=embed_link_value(final_url), inline=False)
                    embed.add_field(
                        name="Expiração",
                        value=(
                            f"{format_duration(signed.expires_in or expires_in)}"
                            + (f"\n`{signed.expires_at}`" if signed.expires_at else "")
                        ),
                        inline=False,
                    )
                else:
                    embed.add_field(name="Link permanente", value=embed_link_value(final_url), inline=False)

                if len(final_url) > DISCORD_BUTTON_URL_MAX:
                    embed.add_field(
                        name="ℹ️ Link longo",
                        value=(
                            "O Discord limita URLs de botões a 512 caracteres. Por isso o botão "
                            "**Abrir arquivo** foi omitido, mas o link continua disponível normalmente."
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

                link_content = message_link_content(final_url)

                # Envia uma cópia por DM. Se o comando já foi usado em DM,
                # a própria resposta da interação já atende esse destino.
                dm_sent = interaction.guild is None
                if interaction.guild is not None:
                    try:
                        await interaction.user.send(
                            content=link_content,
                            embed=embed.copy(),
                            view=make_link_view(final_url, preview_url),
                            file=link_text_file(final_url),
                        )
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
                            "Não consegui enviar a cópia por DM. Provavelmente suas mensagens diretas "
                            "estão fechadas para este servidor. O link acima continua funcionando normalmente."
                        ),
                        inline=False,
                    )

                # No canal/servidor, somente quem executou o comando consegue ver esta mensagem.
                await interaction.followup.send(
                    content=link_content,
                    embed=local_embed,
                    view=make_link_view(final_url, preview_url),
                    file=link_text_file(final_url),
                    ephemeral=True,
                )

                logger.info(
                    "Upload concluído | usuário=%s arquivo=%s tamanho=%s privado=%s dm=%s endpoint=%s",
                    interaction.user.id,
                    attachment.filename,
                    attachment.size,
                    private,
                    dm_sent,
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
                "Hospeda arquivos pelo Discord usando a **File Host API v1**.\n\n"
                "• `POST /uploadFile` para upload\n"
                "• `POST /createSignedUrl` para links temporários privados\n"
                "• Arquivos públicos recebem link permanente\n"
                "• Arquivos privados recebem link assinado com expiração\n"
                "• O resultado é enviado por DM e também como resposta privada no local do comando\n\n"
                "Documentação: https://file-host.base44.app/docs"
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Comando", value="`/hospedar`", inline=True)
        embed.add_field(name="Créditos", value="Knox Dev", inline=True)
        embed.set_footer(
            text="A API não impõe limite de tamanho; anexos enviados pelo Discord ainda obedecem aos limites do Discord."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    settings: Settings = bot.settings  # type: ignore[attr-defined]
    api: FileHostClient = bot.file_host_api  # type: ignore[attr-defined]
    await bot.add_cog(FileHostCog(bot, settings, api))
