from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "sim"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"A variável {name} precisa ser um número inteiro.") from exc


def _valid_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    bot_name: str
    api_functions_url: str
    api_upload_url: str
    api_signed_url: str
    api_shorten_url: str
    connect_timeout: int
    read_timeout: int
    sync_commands: bool

    @property
    def site_url(self) -> str:
        marker = "/functions"
        if marker in self.api_functions_url:
            return self.api_functions_url.split(marker, 1)[0]
        parsed = urlparse(self.api_functions_url)
        return f"{parsed.scheme}://{parsed.netloc}"


def load_settings() -> Settings:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_TOKEN não foi definido no .env.")

    functions_url = os.getenv(
        "API_FUNCTIONS_URL",
        "https://file-host.base44.app/functions",
    ).strip().rstrip("/")
    if not _valid_http_url(functions_url):
        raise RuntimeError("API_FUNCTIONS_URL precisa ser uma URL HTTP/HTTPS válida.")

    upload_url = os.getenv("API_UPLOAD_URL", "").strip() or f"{functions_url}/uploadFile"
    signed_url = os.getenv("API_SIGNED_URL", "").strip() or f"{functions_url}/createSignedUrl"
    shorten_url = os.getenv("API_SHORTEN_URL", "").strip() or f"{functions_url}/shortenUrl"

    if not _valid_http_url(upload_url):
        raise RuntimeError("API_UPLOAD_URL precisa ser uma URL HTTP/HTTPS válida.")
    if not _valid_http_url(signed_url):
        raise RuntimeError("API_SIGNED_URL precisa ser uma URL HTTP/HTTPS válida.")
    if not _valid_http_url(shorten_url):
        raise RuntimeError("API_SHORTEN_URL precisa ser uma URL HTTP/HTTPS válida.")

    connect_timeout = _env_int("API_CONNECT_TIMEOUT", 30)
    read_timeout = _env_int("API_READ_TIMEOUT", 1800)
    if connect_timeout <= 0 or read_timeout <= 0:
        raise RuntimeError("Os timeouts da API precisam ser maiores que zero.")

    return Settings(
        discord_token=token,
        bot_name=os.getenv("BOT_NAME", "Knox File Host").strip() or "Knox File Host",
        api_functions_url=functions_url,
        api_upload_url=upload_url,
        api_signed_url=signed_url,
        api_shorten_url=shorten_url,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        sync_commands=_env_bool("SYNC_COMMANDS", True),
    )
