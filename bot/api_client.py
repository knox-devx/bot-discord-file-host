from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp

from .config import Settings

MAX_SIGNED_URL_SECONDS = 30 * 24 * 60 * 60
MIN_SIGNED_URL_SECONDS = 60


class FileHostError(RuntimeError):
    """Erro amigável retornado pelo cliente da File Host API."""


@dataclass(slots=True, frozen=True)
class UploadResult:
    id: str
    name: str
    file_url: str | None
    file_uri: str | None
    is_private: bool
    mime_type: str | None
    size: int | None
    created_date: str | None
    view_url: str | None
    endpoint: str
    status: int
    raw: dict[str, Any]


@dataclass(slots=True, frozen=True)
class SignedUrlResult:
    signed_url: str
    expires_in: int
    expires_at: str | None
    endpoint: str
    status: int
    raw: dict[str, Any]


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_expiry(expires_in: int) -> int:
    """Valida o intervalo aceito pela API: 60 segundos até 30 dias."""
    if expires_in < MIN_SIGNED_URL_SECONDS or expires_in > MAX_SIGNED_URL_SECONDS:
        raise FileHostError(
            f"expires_in precisa estar entre {MIN_SIGNED_URL_SECONDS} e "
            f"{MAX_SIGNED_URL_SECONDS} segundos."
        )
    return expires_in


def absolute_view_url(site_url: str, view_url: str | None) -> str | None:
    """Converte view_url relativo, como /file/abc123, em URL absoluta."""
    if not view_url:
        return None
    value = view_url.strip()
    if _is_http_url(value):
        return value
    if value.startswith("/"):
        return urljoin(f"{site_url.rstrip('/')}/", value.lstrip("/"))
    return None


def parse_upload_payload(
    payload: Any,
    *,
    endpoint: str,
    status: int,
    site_url: str,
) -> UploadResult:
    if not isinstance(payload, dict):
        raise FileHostError("A API respondeu ao upload, mas o corpo não é um objeto JSON.")

    file_url_raw = payload.get("file_url")
    file_url = (
        file_url_raw.strip()
        if isinstance(file_url_raw, str) and _is_http_url(file_url_raw.strip())
        else None
    )

    file_uri_raw = payload.get("file_uri")
    file_uri = file_uri_raw.strip() if isinstance(file_uri_raw, str) and file_uri_raw.strip() else None

    upload_id = str(payload.get("id") or "").strip()
    name = str(payload.get("name") or "").strip()
    size_raw = payload.get("size")
    size = size_raw if isinstance(size_raw, int) and not isinstance(size_raw, bool) else None
    is_private = payload.get("is_private") is True
    mime_type = payload.get("mime_type") if isinstance(payload.get("mime_type"), str) else None
    created_date = payload.get("created_date") if isinstance(payload.get("created_date"), str) else None
    view_raw = payload.get("view_url") if isinstance(payload.get("view_url"), str) else None
    view_url = absolute_view_url(site_url, view_raw)

    if not upload_id:
        raise FileHostError("A resposta do upload não contém o campo obrigatório 'id'.")
    if not name:
        raise FileHostError("A resposta do upload não contém o campo obrigatório 'name'.")
    if not file_url and not file_uri:
        raise FileHostError("A resposta do upload não contém 'file_url' nem 'file_uri'.")

    return UploadResult(
        id=upload_id,
        name=name,
        file_url=file_url,
        file_uri=file_uri,
        is_private=is_private,
        mime_type=mime_type,
        size=size,
        created_date=created_date,
        view_url=view_url,
        endpoint=endpoint,
        status=status,
        raw=payload,
    )


def parse_signed_payload(payload: Any, *, endpoint: str, status: int) -> SignedUrlResult:
    if not isinstance(payload, dict):
        raise FileHostError("A API respondeu ao link temporário, mas o corpo não é um objeto JSON.")

    signed_url_raw = payload.get("signed_url")
    if not isinstance(signed_url_raw, str) or not _is_http_url(signed_url_raw.strip()):
        raise FileHostError("A resposta não contém um 'signed_url' HTTP/HTTPS válido.")

    expires_raw = payload.get("expires_in")
    expires_in = expires_raw if isinstance(expires_raw, int) and not isinstance(expires_raw, bool) else 0
    expires_at = payload.get("expires_at") if isinstance(payload.get("expires_at"), str) else None

    return SignedUrlResult(
        signed_url=signed_url_raw.strip(),
        expires_in=expires_in,
        expires_at=expires_at,
        endpoint=endpoint,
        status=status,
        raw=payload,
    )


class FileHostClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._timeout = aiohttp.ClientTimeout(
            total=None,
            connect=settings.connect_timeout,
            sock_connect=settings.connect_timeout,
            sock_read=settings.read_timeout,
        )
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def upload(
        self,
        file_path: Path,
        original_name: str,
        content_type: str | None,
        *,
        private: bool = False,
        password: str | None = None,
    ) -> UploadResult:
        """POST /uploadFile usando multipart/form-data conforme a API v1."""
        guessed_type = content_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"

        with file_path.open("rb") as handle:
            form = aiohttp.FormData()
            form.add_field("file", handle, filename=original_name, content_type=guessed_type)
            form.add_field("private", "true" if private else "false")
            if password:
                form.add_field("password", password)

            session = await self._get_session()
            try:
                async with session.post(
                    self.settings.api_upload_url,
                    data=form,
                    allow_redirects=True,
                ) as response:
                    payload = await self._read_json(response)
                    if response.status < 200 or response.status >= 300:
                        raise FileHostError(self._http_error("uploadFile", response.status, payload))

                    return parse_upload_payload(
                        payload,
                        endpoint=str(response.url),
                        status=response.status,
                        site_url=self.settings.site_url,
                    )
            except (aiohttp.ClientError, TimeoutError) as exc:
                raise FileHostError(f"Falha de rede ao chamar uploadFile: {type(exc).__name__}.") from exc

    async def create_signed_url(self, file_uri: str, *, expires_in: int = 3600) -> SignedUrlResult:
        """POST /createSignedUrl para arquivos privados."""
        validate_expiry(expires_in)
        if not file_uri.strip():
            raise FileHostError("file_uri está vazio; não é possível criar um link temporário.")

        session = await self._get_session()
        try:
            async with session.post(
                self.settings.api_signed_url,
                json={"file_uri": file_uri, "expires_in": expires_in},
                allow_redirects=True,
            ) as response:
                payload = await self._read_json(response)
                if response.status < 200 or response.status >= 300:
                    raise FileHostError(self._http_error("createSignedUrl", response.status, payload))

                return parse_signed_payload(payload, endpoint=str(response.url), status=response.status)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise FileHostError(f"Falha de rede ao chamar createSignedUrl: {type(exc).__name__}.") from exc

    async def _read_json(self, response: aiohttp.ClientResponse) -> Any:
        text = await response.text(errors="replace")
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            compact = text.replace("\n", " ").strip()[:500]
            raise FileHostError(f"A API respondeu com conteúdo que não é JSON: {compact}") from exc

    @staticmethod
    def _http_error(operation: str, status: int, payload: Any) -> str:
        try:
            compact = json.dumps(payload, ensure_ascii=False)
        except TypeError:
            compact = str(payload)
        compact = compact.replace("\n", " ").strip()[:500]
        return f"{operation} retornou HTTP {status}: {compact or 'sem corpo de resposta'}"


# Compatibilidade com versões anteriores do projeto.
OmniHostClient = FileHostClient
OmniHostError = FileHostError
