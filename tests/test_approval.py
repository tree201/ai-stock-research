"""权限授权（抓取审批模式）测试：manual/auto/full 三档 gating 与 API 存取。"""

from datetime import date, datetime, timezone
from tempfile import TemporaryDirectory
import os
import unittest
from unittest import mock
from uuid import uuid4

from stock_research import service
from stock_research.documents import DocumentFetchError, FetchedDocument
from stock_research.storage import SQLiteStore


def _fetched(url: str) -> FetchedDocument:
    return FetchedDocument(
        url=url,
        content_type="text/plain",
        body="季报正文".encode("utf-8"),
        fetched_at=datetime.now(timezone.utc),
    )


class _StubFetcher:
    """记录构造参数的 HttpDocumentFetcher 替身，host 校验逻辑与真实一致。"""

    created: list["_StubFetcher"] = []

    def __init__(self, *args, allowed_hosts=(("hkexnews.hk"),), allow_any_host=False, **kwargs) -> None:
        self.allowed_hosts = {host.lower() for host in allowed_hosts}
        self.allow_any_host = allow_any_host
        self.fetched_urls: list[str] = []
        _StubFetcher.created.append(self)

    def fetch(self, url: str) -> FetchedDocument:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
        if host not in self.allowed_hosts and not self.allow_any_host:
            raise DocumentFetchError(f"document host is not allowlisted: {host}")
        self.fetched_urls.append(url)
        return _fetched(url)


class ApprovalModeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = TemporaryDirectory()
        self.db_path = f"{self._dir.name}/research.sqlite3"
        self._env_patch = mock.patch.dict(os.environ, {"AI_STOCK_DB": self.db_path}, clear=False)
        self._env_patch.start()
        self.store = SQLiteStore(self.db_path)

    def tearDown(self) -> None:
        self._env_patch.stop()
        self.store.close()
        self._dir.cleanup()

    def test_default_mode_is_manual(self) -> None:
        self.assertEqual(service.normalize_approval_mode(None), "manual")
        self.assertEqual(service.normalize_approval_mode("bogus"), "manual")
        config = service.llm_config_payload()
        self.assertEqual(config["approval_mode"], "manual")

    def test_set_mode_persists_and_roundtrips_via_config(self) -> None:
        result = service.set_approval_mode_payload({"mode": "full"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["config"]["approval_mode"], "full")
        self.assertEqual(self.store.get_setting(service.APPROVAL_SETTING_KEY), "full")

        again = service.set_approval_mode_payload({"mode": "auto"})
        self.assertEqual(again["config"]["approval_mode"], "auto")

    def test_invalid_mode_rejected(self) -> None:
        with self.assertRaises(ValueError):
            service.set_approval_mode_payload({"mode": "yolo"})


class ApprovalFetchPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.company_id = uuid4()
        _StubFetcher.created = []

    def _run(self, payload: dict, store: SQLiteStore | None = None):
        with mock.patch.object(service, "HttpDocumentFetcher", _StubFetcher):
            return service.research_documents(payload, self.company_id, date(2026, 9, 5), store=store)

    def test_manual_rejects_non_whitelisted_host_with_hint(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self._run({"document_sources": [{"url": "https://example.com/report.pdf"}]})
        self.assertIn("白名单", str(ctx.exception))
        self.assertIn("example.com", str(ctx.exception))
        # 没有任何 fetcher 成功抓取
        self.assertTrue(all(not f.fetched_urls for f in _StubFetcher.created))

    def test_manual_allows_whitelisted_host(self) -> None:
        documents = self._run({"document_sources": [{"url": "https://www1.hkexnews.hk/report.pdf"}]})
        self.assertEqual(len(documents), 1)
        self.assertTrue(_StubFetcher.created[0].fetched_urls)

    def test_manual_whitelist_includes_trusted_hosts(self) -> None:
        store = SQLiteStore(":memory:")
        try:
            store.add_trusted_host("ir.example.com")
            self._run({"document_sources": [{"url": "https://ir.example.com/report.pdf"}]}, store=store)
            self.assertTrue(set(_StubFetcher.created[0].allowed_hosts) >= {"hkexnews.hk", "ir.example.com"})
        finally:
            store.close()

    def test_auto_retries_user_url_outside_whitelist(self) -> None:
        store = SQLiteStore(":memory:")
        try:
            store.set_setting(service.APPROVAL_SETTING_KEY, "auto")
            documents = self._run(
                {"document_sources": [{"url": "https://example.com/report.pdf"}]}, store=store
            )
            self.assertEqual(len(documents), 1)
            retry = _StubFetcher.created[-1]
            self.assertTrue(retry.allow_any_host)
            self.assertEqual(retry.fetched_urls, ["https://example.com/report.pdf"])
        finally:
            store.close()

    def test_full_mode_skips_allowlist_entirely(self) -> None:
        store = SQLiteStore(":memory:")
        try:
            store.set_setting(service.APPROVAL_SETTING_KEY, "full")
            documents = self._run(
                {"document_sources": [{"url": "https://random.example.net/report.pdf"}]}, store=store
            )
            self.assertEqual(len(documents), 1)
            first = _StubFetcher.created[0]
            self.assertTrue(first.allow_any_host)
            self.assertEqual(first.fetched_urls, ["https://random.example.net/report.pdf"])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
