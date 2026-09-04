"""Source-trust classification: whitelist CRUD, ingestion classification, backfill."""

from datetime import date, datetime, timezone
from tempfile import TemporaryDirectory
import os
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from stock_research.documents import FetchedDocument, RawDocument
from stock_research.service import (
    _news_item,
    add_company_source,
    add_trusted_host_payload,
    history_payload,
    research_documents,
    trusted_hosts_payload,
)
from stock_research.storage import SQLiteStore
from stock_research.trust import (
    CLASS_PRIVATE,
    CLASS_PUBLIC,
    TRUST_UNVERIFIED,
    TRUST_VERIFIED,
    TRUST_WHITELIST,
    classify_document,
    host_in_set,
    normalize_host,
    trust_label,
)
from stock_research.web_search import SearchResult


class NormalizeHostTests(unittest.TestCase):
    def test_normalize_host_from_url_and_bare_host(self) -> None:
        self.assertEqual(normalize_host("https://WWW1.HKEXnews.hk/path/x.pdf"), "www1.hkexnews.hk")
        self.assertEqual(normalize_host("Example.COM:8080"), "example.com")
        self.assertEqual(normalize_host(""), "")
        self.assertEqual(normalize_host(None), "")

    def test_host_in_set_tolerates_www_variants(self) -> None:
        trusted = {"hkexnews.hk"}
        self.assertTrue(host_in_set("https://www.hkexnews.hk/a.pdf", trusted))
        self.assertTrue(host_in_set("hkexnews.hk", trusted))
        self.assertFalse(host_in_set("https://evil-hkexnews.hk/a.pdf", trusted))
        self.assertFalse(host_in_set("", trusted))


class ClassifyDocumentTests(unittest.TestCase):
    def test_local_source_types_are_private_verified(self) -> None:
        for source_type in ("user_text", "local_fixture", "local_file"):
            result = classify_document(source_type, "fixture://x", None, lambda host: False)
            self.assertEqual(result, (CLASS_PRIVATE, TRUST_VERIFIED))

    def test_registered_private_source_wins_over_host(self) -> None:
        result = classify_document("url", "https://blog.example.com/a", "private", lambda host: False)
        self.assertEqual(result, (CLASS_PRIVATE, TRUST_VERIFIED))

    def test_registered_public_and_direct_urls_follow_whitelist(self) -> None:
        self.assertEqual(classify_document("url", "https://www.hkexnews.hk/a.pdf", "public", lambda host: True), (CLASS_PUBLIC, TRUST_WHITELIST))
        self.assertEqual(classify_document("url", "https://www.hkexnews.hk/a.pdf", None, lambda host: True), (CLASS_PUBLIC, TRUST_WHITELIST))
        self.assertEqual(classify_document("url", "https://random.news.com/a", None, lambda host: False), (CLASS_PUBLIC, TRUST_UNVERIFIED))

    def test_trust_label_defaults_to_unverified(self) -> None:
        self.assertEqual(trust_label("verified"), "[私有已验证]")
        self.assertEqual(trust_label("whitelist"), "[白名单来源]")
        self.assertEqual(trust_label(None), "[未验证来源]")


class TrustedHostStoreTests(unittest.TestCase):
    def test_fresh_store_seeds_hkex_whitelist(self) -> None:
        store = SQLiteStore()
        try:
            hosts = {row["host"] for row in store.list_trusted_hosts()}
            self.assertTrue({"www1.hkexnews.hk", "www.hkexnews.hk", "hkexnews.hk"} <= hosts)
            self.assertTrue(store.is_trusted_host("https://www1.hkexnews.hk/posting/guide.pdf"))
            self.assertFalse(store.is_trusted_host("https://random.news.com/a"))
        finally:
            store.close()

    def test_add_dedupes_and_removes_hosts(self) -> None:
        store = SQLiteStore()
        try:
            first = store.add_trusted_host("https://www.prexample.com", "测试")
            duplicate = store.add_trusted_host("prexample.com")
            self.assertEqual(first["id"], duplicate["id"])
            self.assertEqual(first["host"], "prexample.com")
            self.assertTrue(store.is_trusted_host("www.prexample.com"))
            with self.assertRaises(ValueError):
                store.add_trusted_host("not a host")
            store.remove_trusted_host(first["id"])
            self.assertFalse(store.is_trusted_host("prexample.com"))
        finally:
            store.close()

    def test_legacy_documents_are_backfilled_by_source_type(self) -> None:
        store = SQLiteStore()
        try:
            company_id = uuid4()
            now = datetime.now(timezone.utc)
            legacy_rows = [
                ("legacy-1", "user_text", "user://document"),
                ("legacy-2", "hkex_filing", "https://www1.hkexnews.hk/a.pdf"),
            ]
            for row_id, source_type, source_url in legacy_rows:
                store.connection.execute(
                    """INSERT INTO documents (id,company_id,source_type,source_url,title,content,content_hash,published_at,source_class,trust)
                       VALUES (?,?,?,?,?,?,?,?,NULL,NULL)""",
                    (row_id, str(company_id), source_type, source_url, "t", "c", f"hash-{row_id}", now.isoformat()),
                )
            store.connection.commit()
            store._backfill_document_trust()
            rows = {row["id"]: row for row in store.connection.execute("SELECT id, source_class, trust FROM documents").fetchall()}
            self.assertEqual(rows["legacy-1"]["source_class"], "private")
            self.assertEqual(rows["legacy-1"]["trust"], "verified")
            self.assertEqual(rows["legacy-2"]["source_class"], "public")
            self.assertEqual(rows["legacy-2"]["trust"], "whitelist")
        finally:
            store.close()


class ResearchDocumentTrustTests(unittest.TestCase):
    class FakeFetcher:
        def __init__(self, **_kwargs):
            pass

        def fetch(self, url: str) -> FetchedDocument:
            return FetchedDocument(url, "text/html", b"<p>Revenue FY2024 HK$ 100 million</p>", datetime.now(timezone.utc))

    def test_ingestion_persists_trust_from_registered_class_and_whitelist(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3"},
            clear=False,
        ):
            store = SQLiteStore(f"{directory}/research.sqlite3")
            try:
                company_id = uuid4()
                store.add_company_source(company_id, "https://ir.private-example.com/results.html", None, "private")
                with patch("stock_research.service.HttpDocumentFetcher", self.FakeFetcher):
                    documents = research_documents(
                        {"document_urls": [
                            "https://ir.private-example.com/results.html",
                            "https://www.hkexnews.hk/a.pdf",
                            "https://random.news.com/a",
                        ]},
                        company_id,
                        date(2025, 12, 31),
                        store=store,
                    )
                by_url = {document.source_url: document for document in documents}
                self.assertEqual(by_url["https://ir.private-example.com/results.html"].trust, "verified")
                self.assertEqual(by_url["https://ir.private-example.com/results.html"].source_class, "private")
                self.assertEqual(by_url["https://www.hkexnews.hk/a.pdf"].trust, "whitelist")
                self.assertEqual(by_url["https://random.news.com/a"].trust, "unverified")
                store.save_documents(documents)
                persisted = {row["source_url"]: row for row in store.list_company_documents(company_id)}
                self.assertEqual(persisted["https://random.news.com/a"]["trust"], "unverified")
                self.assertEqual(persisted["https://ir.private-example.com/results.html"]["trust"], "verified")
            finally:
                store.close()


class NewsTrustTests(unittest.TestCase):
    def test_news_item_trust_follows_whitelist_and_external(self) -> None:
        def item(url: str) -> SearchResult:
            return SearchResult(title="t", url=url, snippet="s")

        trusted = {"hkexnews.hk"}
        whitelisted = _news_item(item("https://www.hkexnews.hk/a"), trusted)
        self.assertEqual(whitelisted["trust"], "whitelist")
        self.assertFalse(whitelisted["external"])
        unknown = _news_item(item("https://random.news.com/a"), trusted)
        self.assertEqual(unknown["trust"], "unverified")
        external = _news_item(item("https://news.google.com/rss/articles/xyz"), trusted)
        self.assertEqual(external["trust"], "unverified")
        self.assertTrue(external["external"])


class TrustedHostApiTests(unittest.TestCase):
    def test_trusted_hosts_crud_via_payload_apis(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3"},
            clear=False,
        ):
            listing = trusted_hosts_payload()
            base_count = len(listing["hosts"])
            added = add_trusted_host_payload({"host": "https://api.finance-example.com/articles", "label": "示例财经"})
            self.assertEqual(added["host"]["host"], "api.finance-example.com")
            listing = history_payload("/api/trusted-hosts")
            self.assertEqual(len(listing["hosts"]), base_count + 1)
            target = next(row for row in listing["hosts"] if row["host"] == "api.finance-example.com")
            from stock_research.service import remove_trusted_host_payload

            remove_trusted_host_payload(str(target["id"]))
            listing = history_payload("/api/trusted-hosts")
            self.assertEqual(len(listing["hosts"]), base_count)

    def test_company_source_with_source_class_round_trips(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AI_STOCK_DB": f"{directory}/research.sqlite3"},
            clear=False,
        ):
            from unittest.mock import patch as _patch

            from stock_research.llm import HeuristicLLMProvider
            from stock_research.service import chat_entry_payload, remove_company_source

            with _patch("stock_research.service.resolve_provider", return_value=HeuristicLLMProvider()):
                chat_entry_payload({"name": "腾讯", "symbol": "00700", "content": "你好"}, db_path=f"{directory}/research.sqlite3")
            added = add_company_source("00700", "HK", "https://ir.private-example.com/results.html", "私有公告", "private")
            self.assertEqual(added["source"]["source_class"], "private")
            panel = history_payload("/api/company-panel?symbol=00700&market=HK")
            self.assertEqual(panel["sources"][0]["source_class"], "private")
            remove_company_source(added["source"]["id"])
