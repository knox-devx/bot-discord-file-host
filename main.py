from __future__ import annotations

import logging
import sys

import discord
from discord.ext import commands

from bot.api_client import FileHostClient
from bot.config import Settings, load_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("knox-file-host")


class FileHostBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        self.file_host_api = FileHostClient(settings)

    async def setup_hook(self) -> None:
        await self.load_extension("bot.cogs.files")
        if self.settings.sync_commands:
            synced = await self.tree.sync()
            logger.info("%s comando(s) slash sincronizado(s).", len(synced))

    async def on_ready(self) -> None:
        if self.user is None:
            return
        logger.info("%s conectado como %s (%s)", self.settings.bot_name, self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="/hospedar • arquivos",
            )
        )

    async def close(self) -> None:
        await self.file_host_api.close()
        await super().close()


def main() -> None:
    try:
        settings = load_settings()
    except RuntimeError as exc:
        logger.error("Configuração inválida: %s", exc)
        sys.exit(1)

    bot = FileHostBot(settings)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
