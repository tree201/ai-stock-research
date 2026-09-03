"""Raw-document and citation-friendly evidence primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import hashlib
from html.parser import HTMLParser
import subprocess
from typing import Any, Callable, Iterable
from uuid import UUID, uuid4
from urllib.parse import urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class RawDocument:
    company_id: UUID
    source_type: str
    source_url: str
    title: str
    content: str
    published_at: datetime | None = None
    period_start: date | None = None
    period_end: date | None = None
    language: str | None = None
    id: UUID = field(default_factory=uuid4)
    page_starts: tuple[int, ...] = ()

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EvidenceChunk:
    document_id: UUID
    chunk_index: int
    text: str
    start_line: int
    end_line: int
    published_at: datetime | None = None
    page: int | None = None
    section: str | None = None
    id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    """An immutable response captured before parsing."""

    url: str
    content_type: str
    body: bytes
    fetched_at: datetime
    status_code: int = 200
    etag: str | None = None
    last_modified: str | None = None

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


class DocumentFetchError(RuntimeError):
    """Raised when a document cannot be safely downloaded."""


class UnsupportedDocumentType(DocumentFetchError):
    """Raised when no parser is registered for a document type."""


class PdfParserError(DocumentFetchError):
    """Raised when the PDF parser cannot produce text."""


class HttpDocumentFetcher:
    """Small, guarded HTTP fetcher for public filings and IR documents.

    The default allowlist deliberately excludes arbitrary hosts.  Production
    deployments should configure it with the exact HKEX/company domains they
    have reviewed and are permitted to access.
    """

    def __init__(
        self,
        opener: Callable[..., Any] = urlopen,
        allowed_hosts: Iterable[str] = ("www1.hkexnews.hk", "www.hkexnews.hk", "hkexnews.hk"),
        max_bytes: int = 25 * 1024 * 1024,
        user_agent: str = "ai-stock-research/0.1 (+document-fetcher)",
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._opener = opener
        self._allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self._max_bytes = max_bytes
        self._user_agent = user_agent

    def fetch(self, url: str) -> FetchedDocument:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https":
            raise DocumentFetchError("document URLs must use https")
        if host not in self._allowed_hosts:
            raise DocumentFetchError(f"document host is not allowlisted: {host or '<missing>'}")

        request = Request(url, headers={"User-Agent": self._user_agent, "Accept": "text/html, application/pdf, text/plain"})
        try:
            with self._opener(request, timeout=20) as response:
                status_code = int(getattr(response, "status", 200))
                content_type = (response.headers.get("Content-Type", "") or "").split(";", 1)[0].strip().lower()
                declared_length = response.headers.get("Content-Length")
                if declared_length and int(declared_length) > self._max_bytes:
                    raise DocumentFetchError("document exceeds configured size limit")
                body = response.read(self._max_bytes + 1)
                etag = response.headers.get("ETag")
                last_modified = response.headers.get("Last-Modified")
        except DocumentFetchError:
            raise
        except Exception as exc:
            raise DocumentFetchError(f"failed to fetch document: {exc}") from exc

        if len(body) > self._max_bytes:
            raise DocumentFetchError("document exceeds configured size limit")
        if status_code < 200 or status_code >= 300:
            raise DocumentFetchError(f"document request returned HTTP {status_code}")
        if not content_type:
            content_type = self._guess_content_type(url, body)
        return FetchedDocument(url, content_type, body, datetime.now(timezone.utc), status_code, etag, last_modified)

    @staticmethod
    def _guess_content_type(url: str, body: bytes) -> str:
        path = urlparse(url).path.lower()
        if path.endswith(".pdf") or body.startswith(b"%PDF"):
            return "application/pdf"
        if path.endswith((".html", ".htm")) or b"<html" in body[:512].lower():
            return "text/html"
        return "text/plain"


class _VisibleTextParser(HTMLParser):
    _ignored = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._ignored:
            self._ignored_depth += 1
        elif tag.lower() in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._ignored and self._ignored_depth:
            self._ignored_depth -= 1
        elif tag.lower() in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "section"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            self.parts.append(data)


def extract_text(fetched: FetchedDocument) -> str:
    """Extract visible text for supported document types.

    PDF bytes are intentionally not guessed or decoded as UTF-8.  Until a
    real PDF parser is configured, callers receive an explicit error.
    """

    if fetched.content_type in {"text/html", "application/xhtml+xml"}:
        parser = _VisibleTextParser()
        parser.feed(fetched.body.decode("utf-8", errors="replace"))
        return _normalize_text("".join(parser.parts))
    if fetched.content_type.startswith("text/"):
        return _normalize_text(fetched.body.decode("utf-8", errors="replace"))
    if fetched.content_type == "application/pdf" or fetched.body.startswith(b"%PDF"):
        raise UnsupportedDocumentType("PDF parser is not configured yet; retain the raw document for a parser worker")
    raise UnsupportedDocumentType(f"unsupported document type: {fetched.content_type or '<unknown>'}")


@dataclass(frozen=True, slots=True)
class ParsedPage:
    page: int
    text: str


class PdfTextExtractor:
    """Extract layout-preserving text and page boundaries using pdftotext.

    Poppler is an external, well-scoped dependency.  The runner is injectable
    so unit tests do not need a PDF fixture or a local Poppler installation.
    """

    def __init__(self, runner: Callable[[bytes, float], tuple[int, bytes, bytes]] | None = None, timeout: float = 30.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._runner = runner or self._run_pdftotext
        self._timeout = timeout

    def extract_pages(self, body: bytes) -> list[ParsedPage]:
        if not body.startswith(b"%PDF"):
            raise PdfParserError("PDF body does not have a valid header")
        return_code, stdout, stderr = self._runner(body, self._timeout)
        if return_code != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise PdfParserError(f"pdftotext failed ({return_code}): {detail or 'unknown error'}")
        pages = []
        for index, text in enumerate(stdout.decode("utf-8", errors="replace").split("\f"), start=1):
            normalized = _normalize_text(text)
            if normalized:
                pages.append(ParsedPage(page=index, text=normalized))
        if not pages:
            raise PdfParserError("PDF parser returned no text")
        return pages

    def extract_chunks(self, document_id: UUID, body: bytes, max_chars: int = 1200) -> list[EvidenceChunk]:
        if max_chars < 100:
            raise ValueError("max_chars must be at least 100")
        chunks: list[EvidenceChunk] = []
        for parsed_page in self.extract_pages(body):
            lines = parsed_page.text.splitlines()
            current: list[str] = []
            start_line = 1

            def flush(end_line: int) -> None:
                if not current:
                    return
                text = "\n".join(current).strip()
                if text:
                    chunks.append(
                        EvidenceChunk(
                            document_id=document_id,
                            chunk_index=len(chunks),
                            text=text,
                            start_line=start_line,
                            end_line=end_line,
                            page=parsed_page.page,
                        )
                    )

            for line_number, line in enumerate(lines, start=1):
                candidate = "\n".join(current + [line])
                if current and len(candidate) > max_chars:
                    flush(line_number - 1)
                    current = [line]
                    start_line = line_number
                else:
                    current.append(line)
            flush(len(lines))
        return chunks

    @staticmethod
    def _run_pdftotext(body: bytes, timeout: float) -> tuple[int, bytes, bytes]:
        try:
            completed = subprocess.run(
                ["pdftotext", "-layout", "-", "-"],
                input=body,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return 127, b"", str(exc).encode("utf-8")
        return completed.returncode, completed.stdout, completed.stderr


def _normalize_text(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


class DocumentIngestor:
    """Split text while preserving enough location metadata for citations."""

    def __init__(self, max_chars: int = 1200) -> None:
        if max_chars < 100:
            raise ValueError("max_chars must be at least 100")
        self.max_chars = max_chars

    def chunk(self, document: RawDocument) -> list[EvidenceChunk]:
        lines = document.content.splitlines()
        if not lines:
            return []
        chunks: list[EvidenceChunk] = []
        current: list[str] = []
        start_line = 1

        def page_for(line_number: int) -> int | None:
            if not document.page_starts:
                return None
            return sum(1 for value in document.page_starts if value <= line_number) or None

        def flush(end_line: int) -> None:
            if not current:
                return
            text = "\n".join(current).strip()
            if text:
                chunks.append(
                    EvidenceChunk(
                        document_id=document.id,
                        chunk_index=len(chunks),
                        text=text,
                        start_line=start_line,
                        end_line=end_line,
                        published_at=document.published_at,
                        page=page_for(start_line),
                    )
                )

        for line_number, line in enumerate(lines, start=1):
            if current and line_number in document.page_starts[1:]:
                flush(line_number - 1)
                current = []
                start_line = line_number
            candidate = "\n".join(current + [line])
            if current and len(candidate) > self.max_chars:
                flush(line_number - 1)
                current = [line]
                start_line = line_number
            else:
                current.append(line)
        flush(len(lines))
        return chunks

    def chunk_many(self, documents: Iterable[RawDocument]) -> list[EvidenceChunk]:
        chunks: list[EvidenceChunk] = []
        for document in documents:
            chunks.extend(self.chunk(document))
        return chunks
