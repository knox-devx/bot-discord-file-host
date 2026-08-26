from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

from .config import Settings


URL_KEYS = (
    "file_url",
    "fileUrl",
    "url",
    "link",
    "download_url",
    "downloadUrl",
    "public_url",
    "publicUrl",
)


class OmniHostError(RuntimeError):
    """Erro amigável retornado pelo cliente da API de hospedagem."""


@dataclass(slots=True, frozen=True)
class UploadResult:
    url: str
    endpoint: str
    status: int
    raw: Any


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def extract_url(payload: Any) -> str | None:
    """Procura uma URL de arquivo em respostas JSON ou texto."""
    if isinstance(payload, str):
        value = payload.strip().strip('"')
        return value if _is_http_url(value) else None

    if isinstance(payload, dict):
        for key in URL_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and _is_http_url(value.strip()):
                return value.strip()

        # Alguns backends embrulham a resposta em data/result/file.
        for key in ("data", "result", "file", "upload"):
            if key in payload:
                found = extract_url(payload[key])
                if found:
                    return found

        # Último fallback: busca recursiva em qualquer campo.
        for value in payload.values():
            found = extract_url(value)
            if found:
                return found

    if isinstance(payload, list):
        for value in payload:
            found = extract_url(value)
            if found:
                return found

    return None


class OmniHostClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        timeout = aiohttp.ClientTimeout(
            total=None,
            connect=settings.connect_timeout,
            sock_connect=settings.connect_timeout,
            sock_read=settings.read_timeout,
        )
        self._timeout = timeout
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        # A sessão é criada somente quando já existe um loop assíncrono ativo.
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    def candidate_endpoints(self) -> list[str]:
        if self.settings.api_upload_url:
            return [self.settings.api_upload_url]

        base = self.settings.api_base_url.rstrip("/")
        endpoints: list[str] = []
        for function_name in self.settings.api_functions:
            # Formato atual documentado pelo Base44.
            endpoints.append(f"{base}/functions/{function_name}")
            # Compatibilidade com a URL provável fornecida para este app.
            endpoints.append(f"{base}/base44/functions/{function_name}")

        # Remove duplicatas preservando a ordem.
        return list(dict.fromkeys(endpoints))

    async def upload(self, file_path: Path, original_name: str, content_type: str | None) -> UploadResult:
        errors: list[str] = []
        guessed_type = content_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"

        for endpoint in self.candidate_endpoints():
            try:
                result = await self._upload_once(
                    endpoint=endpoint,
                    file_path=file_path,
                    original_name=original_name,
                    content_type=guessed_type,
                )
                if result:
                    return result
            except OmniHostError as exc:
                errors.append(f"{endpoint}: {exc}")
            except (aiohttp.ClientError, TimeoutError) as exc:
                errors.append(f"{endpoint}: falha de rede ({type(exc).__name__})")

        details = "\n".join(errors[-6:]) if errors else "Nenhum endpoint foi tentado."
        raise OmniHostError(
            "A API não aceitou o upload em nenhuma rota configurada. "
            "Se a documentação indicar uma rota específica, coloque-a em API_UPLOAD_URL.\n"
            f"Detalhes:\n{details}"
        )

    async def _upload_once(
        self,
        *,
        endpoint: str,
        file_path: Path,
        original_name: str,
        content_type: str,
    ) -> UploadResult:
        with file_path.open("rb") as handle:
            form = aiohttp.FormData()
            form.add_field(
                "file",
                handle,
                filename=original_name,
                content_type=content_type,
            )

            session = await self._get_session()
            async with session.post(
                endpoint,
                data=form,
                headers=self.settings.api_headers,
                allow_redirects=True,
            ) as response:
                text = await response.text(errors="replace")
                payload: Any = text
                if text:
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        pass

                if response.status < 200 or response.status >= 300:
                    compact = text.replace("\n", " ").strip()[:500]
                    raise OmniHostError(f"HTTP {response.status}: {compact or 'sem corpo de resposta'}")

                url = extract_url(payload)
                if not url:
                    compact = text.replace("\n", " ").strip()[:500]
                    raise OmniHostError(
                        "upload respondeu com sucesso, mas não encontrei uma URL na resposta: "
                        f"{compact or '<vazio>'}"
                    )

                return UploadResult(
                    url=url,
                    endpoint=str(response.url),
                    status=response.status,
                    raw=payload,
                )
