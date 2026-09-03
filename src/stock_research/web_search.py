"""Free, keyless web search used for chat follow-ups.

DuckDuckGo's HTML endpoint covers general pages but serves an anomaly page to
Python's TLS fingerprint and rate-limits aggressively, so its fetch goes
through the system ``curl`` binary and results are best-effort.  When it
yields nothing, the Google News RSS feed (an official, stable, keyless feed)
acts as the fallback.  Callers degrade to the report-grounded answer path
when both return nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re
import subprocess
import xml.etree.ElementTree as ET
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    published: str | None = None


_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_TITLE_RE = re.compile(r'class="result__a"\s+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


class DuckDuckGoSearch:
    """Query DuckDuckGo's HTML endpoint and return title/url/snippet rows."""

    provider_name = "duckduckgo_html"

    def __init__(self, fetch: Callable[[str], str] | None = None, timeout: float = 10.0) -> None:
        self._timeout = timeout
        self._fetch = fetch or self._curl_fetch

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not query.strip():
            return []
        url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
        try:
            body = self._fetch(url)
        except Exception:
            # The search path is best-effort; a blocked endpoint or missing
            # curl must not break the chat reply.
            return []
        return self._parse(body)[:max_results]

    def _curl_fetch(self, url: str) -> str:
        completed = subprocess.run(
            ["curl", "-sS", "-f", "-m", str(int(self._timeout)), "-A", _USER_AGENT, "-H", "Accept: text/html", url],
            capture_output=True,
            text=True,
            check=True,
        )
        return completed.stdout

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


class GoogleNewsSearch:
    """Query Google News' official RSS feed and return news title/link rows."""

    provider_name = "google_news_rss"

    def __init__(self, opener: Callable[..., Any] = urlopen, timeout: float = 10.0) -> None:
        self._opener = opener
        self._timeout = timeout

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not query.strip():
            return []
        url = f"https://news.google.com/rss/search?q={quote(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
        request = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml"})
        try:
            with self._opener(request, timeout=self._timeout) as response:
                body = response.read().decode("utf-8", "replace")
        except Exception:
            return []
        return self._parse(body)[:max_results]

    @staticmethod
    def _parse(body: str) -> list[SearchResult]:
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return []
        results: list[SearchResult] = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            source = (item.findtext("source") or "").strip()
            published = (item.findtext("pubDate") or "").strip() or None
            if title and link:
                results.append(
                    SearchResult(title=title, url=link, snippet=source, published=published)
                )
        return results
