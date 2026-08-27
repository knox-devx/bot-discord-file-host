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
    api_base_url: str
    api_upload_url: str | None
    api_functions: tuple[str, ...]
    api_key: str | None
    api_key_header: str
    api_key_prefix: str
    connect_timeout: int
    read_timeout: int
    sync_commands: bool

    @property
    def api_headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}

        value = self.api_key
        if self.api_key_prefix:
            value = f"{self.api_key_prefix} {value}"
        return {self.api_key_header: value}


def load_settings() -> Settings:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_TOKEN não foi definido no .env.")

    base_url = os.getenv(
        "API_BASE_URL",
        "https://file-host.base44.app",
    ).strip().rstrip("/")
    if not _valid_http_url(base_url):
        raise RuntimeError("API_BASE_URL precisa ser uma URL HTTP/HTTPS válida.")

    exact_upload_url = os.getenv("API_UPLOAD_URL", "").strip() or None
    if exact_upload_url and not _valid_http_url(exact_upload_url):
        raise RuntimeError("API_UPLOAD_URL precisa ser uma URL HTTP/HTTPS válida.")

    raw_functions = os.getenv("API_FUNCTIONS", "upload,upload-file,host-file")
    functions = tuple(
        item.strip().strip("/")
        for item in raw_functions.split(",")
        if item.strip().strip("/")
    )
    if not functions and exact_upload_url is None:
        raise RuntimeError("Defina API_UPLOAD_URL ou ao menos uma função em API_FUNCTIONS.")

    return Settings(
        discord_token=token,
        bot_name=os.getenv("BOT_NAME", "Knox File Host").strip() or "Knox File Host",
        api_base_url=base_url,
        api_upload_url=exact_upload_url,
        api_functions=functions,
        api_key=os.getenv("API_KEY", "").strip() or None,
        api_key_header=os.getenv("API_KEY_HEADER", "Authorization").strip() or "Authorization",
        api_key_prefix=os.getenv("API_KEY_PREFIX", "Bearer").strip(),
        connect_timeout=_env_int("API_CONNECT_TIMEOUT", 30),
        read_timeout=_env_int("API_READ_TIMEOUT", 1800),
        sync_commands=_env_bool("SYNC_COMMANDS", True),
    )
