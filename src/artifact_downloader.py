"""Download de artefatos com detecção de formato e deduplicação por SHA-256."""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    from .retry import retry_delay
    from .ssl_context import criar_contexto_ssl
except ImportError:  # permite executar o módulo diretamente
    from retry import retry_delay
    from ssl_context import criar_contexto_ssl


def _safe_filename(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
    return cleaned.strip("_") or "arquivo"


_SUFIXOS_CONHECIDOS = {".pdf", ".html", ".htm", ".aspx", ".txt", ".json", ".jsonl", ".zip", ".csv"}
_NOME_NO_CABECALHO = re.compile(r"""filename\*?=(?:UTF-8'')?"?([^";]+)"?""", re.IGNORECASE)


def _extension(url: str, content_type: str, body: bytes = b"", disposition: str = "") -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in _SUFIXOS_CONHECIDOS:
        return suffix
    # Endpoints como /pca/{ano}/csv não têm extensão no caminho, mas mandam o
    # nome real do arquivo em Content-Disposition.
    nome = _NOME_NO_CABECALHO.search(disposition or "")
    if nome:
        suffix = Path(nome.group(1).strip()).suffix.lower()
        if suffix in _SUFIXOS_CONHECIDOS:
            return suffix
    if body.startswith(b"%PDF-"):
        return ".pdf"
    if body.startswith(b"PK\x03\x04"):
        return ".zip"
    mime = content_type.lower().split(";", 1)[0].strip()
    return {
        "application/pdf": ".pdf",
        "application/json": ".json",
        "text/json": ".json",
        "application/jsonl": ".jsonl",
        "application/x-ndjson": ".jsonl",
        "application/ndjson": ".jsonl",
        "text/html": ".html",
        "application/xhtml+xml": ".html",
        "application/xml": ".xml",
        "text/plain": ".txt",
        "text/csv": ".csv",
    }.get(mime, ".bin")


@dataclass
class ArtifactDownloader:
    """Baixa para um repositório compartilhado, sem duplicar conteúdo."""

    root: Path
    output_dir: Path
    download_limit: int = 30 * 1024 * 1024
    retryable_http: frozenset[int] = field(
        default_factory=lambda: frozenset({408, 425, 429, 500, 502, 503, 504})
    )

    def _find_existing_by_hash(self, digest: str) -> Path | None:
        artifact_root = self.output_dir / "arquivos"
        if not artifact_root.exists():
            return None
        for path in artifact_root.rglob("*"):
            if path.is_file():
                current = hashlib.sha256(path.read_bytes()).hexdigest()
                if current == digest:
                    return path
        return None

    def save_bytes(
        self,
        body: bytes,
        destination_dir: Path,
        filename_prefix: str,
        extension: str,
        *,
        deduplicate: bool = True,
    ) -> dict[str, object]:
        """Salva bytes únicos; o hash é a identidade do conteúdo."""
        destination_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(body).hexdigest()
        existing_path = self._find_existing_by_hash(digest) if deduplicate else None
        if existing_path:
            return {
                "status": "reutilizado",
                "bytes": len(body),
                "sha256": digest,
                "arquivo": str(existing_path.relative_to(self.root)),
                "observacao": "conteúdo já existente; arquivo não duplicado",
            }
        normalized_extension = extension if extension.startswith(".") else "." + extension
        filename = _safe_filename(filename_prefix) + "_" + digest[:12] + normalized_extension
        path = destination_dir / filename
        path.write_bytes(body)
        try:
            relative_path = str(path.relative_to(self.root))
        except ValueError:
            relative_path = str(path)
        return {
            "status": "baixado",
            "bytes": len(body),
            "sha256": digest,
            "arquivo": relative_path,
        }

    def download_url(
        self,
        url: str,
        destination_dir: Path,
        filename_prefix: str,
        *,
        attempts: int = 3,
        timeout: float = 20.0,
        headers: dict[str, str] | None = None,
        deduplicate: bool = True,
    ) -> dict[str, object]:
        destination_dir.mkdir(parents=True, exist_ok=True)
        request_headers = {
            "Accept": "application/pdf, text/html, application/xhtml+xml, text/plain, */*",
            "User-Agent": "rag-grande-desafio/1.0",
        }
        request_headers.update(headers or {})
        request = Request(url, headers=request_headers)
        last_error = ""
        for attempt in range(1, max(1, attempts) + 1):
            try:
                with urlopen(request, timeout=timeout, context=criar_contexto_ssl()) as response:
                    body = response.read(self.download_limit + 1)
                    content_type = response.headers.get("Content-Type", "")
                    if len(body) > self.download_limit:
                        return {
                            "status": "erro_download",
                            "http_status": response.status,
                            "tentativas": attempt,
                            "content_type": content_type,
                            "url": url,
                            "url_final": response.geturl(),
                            "observacao": "arquivo excede o limite de 30 MiB e não foi salvo",
                        }

                    saved = self.save_bytes(
                        body,
                        destination_dir,
                        filename_prefix,
                        _extension(
                            response.geturl(),
                            content_type,
                            body,
                            response.headers.get("Content-Disposition", ""),
                        ),
                        deduplicate=deduplicate,
                    )
                    return {
                        **saved,
                        "http_status": response.status,
                        "tentativas": attempt,
                        "content_type": content_type,
                        "url": url,
                        "url_final": response.geturl(),
                        "etag": response.headers.get("ETag"),
                        "last_modified": response.headers.get("Last-Modified"),
                    }
            except HTTPError as error:
                last_error = str(error.reason)
                if error.code == 304:
                    return {
                        "status": "nao_modificado",
                        "http_status": 304,
                        "tentativas": attempt,
                        "content_type": error.headers.get("Content-Type", "") if error.headers else "",
                        "url": url,
                        "etag": error.headers.get("ETag") if error.headers else None,
                        "last_modified": error.headers.get("Last-Modified") if error.headers else None,
                    }
                if error.code in self.retryable_http and attempt < max(1, attempts):
                    time.sleep(retry_delay(attempt, error.headers.get("Retry-After")))
                    continue
                return {
                    "status": "erro_download",
                    "http_status": error.code,
                    "tentativas": attempt,
                    "content_type": error.headers.get("Content-Type", "") if error.headers else "",
                    "url": url,
                    "erro": last_error,
                }
            except (TimeoutError, URLError, OSError) as error:
                last_error = str(getattr(error, "reason", error))
                if attempt < max(1, attempts):
                    time.sleep(retry_delay(attempt, None))
                    continue
                return {
                    "status": "erro_download",
                    "http_status": None,
                    "tentativas": attempt,
                    "content_type": "",
                    "url": url,
                    "erro": last_error,
                }
        return {"status": "erro_download", "url": url, "erro": last_error}
