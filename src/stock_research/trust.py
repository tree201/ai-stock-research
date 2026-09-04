"""Source trust classification shared by ingestion, lookups and prompts.

Three trust levels back the UI badges and the LLM evidence tags:
- verified:  user-registered/local private material (human confirmed)
- whitelist: public web source whose host is on the trusted-host whitelist
- unverified: any other public web source
"""

from __future__ import annotations

from typing import Iterable
from urllib.parse import urlparse

CLASS_PRIVATE = "private"
CLASS_PUBLIC = "public"

TRUST_VERIFIED = "verified"
TRUST_WHITELIST = "whitelist"
TRUST_UNVERIFIED = "unverified"

TRUST_LABELS = {
    TRUST_VERIFIED: "[私有已验证]",
    TRUST_WHITELIST: "[白名单来源]",
    TRUST_UNVERIFIED: "[未验证来源]",
}

_LOCAL_SOURCE_TYPES = frozenset({"user_text", "local_fixture", "local_file"})

DEFAULT_TRUSTED_HOSTS = frozenset({"www1.hkexnews.hk", "www.hkexnews.hk", "hkexnews.hk"})


def normalize_host(url_or_host: str | None) -> str:
    """Lowercase hostname from either a bare host or a full URL."""
    value = str(url_or_host or "").strip().lower()
    if not value:
        return ""
    if "://" in value:
        value = urlparse(value).hostname or ""
    if ":" in value:
        value = value.split(":", 1)[0]
    return value


def host_in_set(url_or_host: str | None, trusted: Iterable[str]) -> bool:
    """Match a host (or URL) against a trusted set, tolerating www. variants."""
    normalized = normalize_host(url_or_host)
    if not normalized:
        return False
    bare = normalized[4:] if normalized.startswith("www.") else normalized
    return any(candidate in trusted for candidate in (normalized, bare, f"www.{bare}"))


def classify_document(
    source_type: str,
    source_url: str,
    registered_class: str | None,
    is_trusted: Iterable[str] | Callable,
) -> tuple[str, str]:
    """Return ``(source_class, trust)`` for a document at ingestion time.

    ``is_trusted`` is either a predicate or a collection of trusted hosts;
    trust is judged once here and then persisted (whitelist edits never
    rewrite history).
    """
    if callable(is_trusted):
        checker = is_trusted
    else:
        checker = lambda host: host_in_set(host, is_trusted)  # noqa: E731
    if source_type in _LOCAL_SOURCE_TYPES or registered_class == CLASS_PRIVATE:
        return CLASS_PRIVATE, TRUST_VERIFIED
    if checker(normalize_host(source_url)):
        return CLASS_PUBLIC, TRUST_WHITELIST
    return CLASS_PUBLIC, TRUST_UNVERIFIED


def trust_label(trust: str | None) -> str:
    """Chinese tag injected into LLM prompts next to evidence items."""
    return TRUST_LABELS.get(trust or TRUST_UNVERIFIED, TRUST_LABELS[TRUST_UNVERIFIED])
