from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from bot.api_client import OmniHostClient, OmniHostError
from bot.config import Settings

logger = logging.getLogger(__name__)


def format_bytes(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def safe_filename(name: str) -> str:
    # O nome real segue no multipart; isto serve apenas para o arquivo temporário local.
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "._- ").strip()
    return cleaned[:180] or "arquivo.bin"


class LinkView(discord.ui.View):
    def __init__(self, url: str) -> None:
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Abrir arquivo", url=url, emoji="🔗"))


class FileHostCog(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings, api: OmniHostClient) -> None:
        self.bot = bot
        self.settings = settings
        self.api = api
        self._user_locks: dict[int, asyncio.Lock] = {}

    def _lock_for(self, user_id: int) -> asyncio.Lock:
        # Um upload por usuário por vez evita duplicação acidental sem criar cooldown
        # ou limite de quantidade de arquivos.
        return self._user_locks.setdefault(user_id, asyncio.Lock())

    async def _host_attachment(
        self,
        interaction: discord.Interaction,
        attachment: discord.Attachment,
        *,
        private: bool,
    ) -> None:
        lock = self._lock_for(interaction.user.id)
        if lock.locked():
            await interaction.response.send_message(
                "Você já tem um upload em andamento. Termine esse envio antes de iniciar outro.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=private)

        async with lock:
            temp_path: Path | None = None
            try:
                suffix = Path(safe_filename(attachment.filename)).suffix
                with tempfile.NamedTemporaryFile(prefix="knox-host-", suffix=suffix, delete=False) as temp:
                    temp_path = Path(temp.name)

                # Faz download diretamente do CDN do Discord para o arquivo temporário.
                await attachment.save(temp_path, use_cached=True)

                result = await self.api.upload(
                    file_path=temp_path,
                    original_name=attachment.filename,
                    content_type=attachment.content_type,
                )

                embed = discord.Embed(
                    title="✅ Arquivo hospedado",
                    description=f"Seu arquivo foi enviado com sucesso pelo **{self.settings.bot_name}**.",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Arquivo", value=f"`{attachment.filename[:100]}`", inline=False)
                embed.add_field(name="Tamanho", value=format_bytes(attachment.size), inline=True)
                embed.add_field(name="Tipo", value=f"`{attachment.content_type or 'desconhecido'}`", inline=True)
                embed.add_field(name="Link", value=result.url, inline=False)
                embed.set_footer(text="Mantido por Knox Dev")

                await interaction.followup.send(
                    embed=embed,
                    view=LinkView(result.url),
                    ephemeral=private,
                )
                logger.info(
                    "Upload concluído | usuário=%s arquivo=%s tamanho=%s endpoint=%s",
                    interaction.user.id,
                    attachment.filename,
                    attachment.size,
                    result.endpoint,
                )
            except OmniHostError as exc:
                logger.warning("Falha na API ao hospedar %s: %s", attachment.filename, exc)
                details = str(exc)
                if len(details) > 1700:
                    details = details[:1700] + "…"
                await interaction.followup.send(
                    "❌ **Não consegui hospedar o arquivo.**\n"
                    "A API rejeitou o upload ou a rota configurada não corresponde à documentação.\n\n"
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

    @app_commands.command(name="hospedar", description="Hospeda um arquivo e devolve um link público.")
    @app_commands.describe(
        arquivo="Arquivo que será enviado para a hospedagem.",
        privado="Se ativado, somente você verá a resposta do bot.",
    )
    async def hospedar(
        self,
        interaction: discord.Interaction,
        arquivo: discord.Attachment,
        privado: bool = False,
    ) -> None:
        await self._host_attachment(interaction, arquivo, private=privado)

    @app_commands.command(name="sobre", description="Mostra informações sobre o serviço de hospedagem.")
    async def sobre(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title=f"📦 {self.settings.bot_name}",
            description=(
                "Hospeda anexos enviados pelo Discord usando a API Omni Host/Base44 e devolve um link.\n\n"
                "O bot **não define limite próprio de tamanho ou quantidade**. Ainda se aplicam os limites "
                "de upload do Discord, da hospedagem onde o bot roda e da API utilizada."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Comando", value="`/hospedar`", inline=True)
        embed.add_field(name="Créditos", value="Knox Dev", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    settings: Settings = bot.settings  # type: ignore[attr-defined]
    api: OmniHostClient = bot.omni_api  # type: ignore[attr-defined]
    await bot.add_cog(FileHostCog(bot, settings, api))
