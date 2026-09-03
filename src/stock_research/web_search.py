"""Free DuckDuckGo HTML search used for chat follow-ups.

The endpoint is unofficial and may be rate-limited, so callers degrade to the
report-grounded answer path when no results come back.  Each result keeps its
title, URL and snippet so answers can cite links instead of inventing facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str


_TITLE_RE = re.compile(r'class="result__a"\s+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


class DuckDuckGoSearch:
    """Query DuckDuckGo's HTML endpoint and return title/url/snippet rows."""

    provider_name = "duckduckgo_html"

    def __init__(
        self,
        opener: Callable[..., Any] = urlopen,
        user_agent: str = "ai-stock-research/0.1 (+search)",
        timeout: float = 10.0,
    ) -> None:
        self._opener = opener
        self._user_agent = user_agent
        self._timeout = timeout

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not query.strip():
            return []
        url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
        request = Request(url, headers={"User-Agent": self._user_agent, "Accept": "text/html"})
        try:
            with self._opener(request, timeout=self._timeout) as response:
                body = response.read().decode("utf-8", "replace")
        except Exception:
            # The search path is best-effort; an unavailable endpoint or a
            # malformed response must not break the chat reply.
            return []
        return self._parse(body)[:max_results]

    @staticmethod
    def _parse(body: str) -> list[SearchResult]:
        results: list[SearchResult] = []
        for chunk in body.split('result__body"')[1:]:
            title_match = _TITLE_RE.search(chunk)
            if title_match is None:
                continue
            url = _decode_redirect(title_match.group(1))
            if not url:
                continue
            title = _TAG_RE.sub("", unescape(title_match.group(2))).strip()
            snippet_match = _SNIPPET_RE.search(chunk)
            snippet = (
                _TAG_RE.sub("", unescape(snippet_match.group(1))).strip()
                if snippet_match
                else ""
            )
            results.append(SearchResult(title=title, url=url, snippet=snippet))
        return results


def _decode_redirect(href: str) -> str:
    """Resolve DuckDuckGo's ``//duckduckgo.com/l/?uddg=...`` wrapper."""
    parsed = urlparse(href if "://" in href else f"https:{href}")
    target = (parse_qs(parsed.query).get("uddg") or [""])[0]
    return target or href
