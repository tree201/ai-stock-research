from datetime import date, datetime, timezone
import json
from uuid import uuid4
import unittest

from stock_research.documents import (
    DocumentFetchError,
    DocumentIngestor,
    FetchedDocument,
    HttpDocumentFetcher,
    PdfParserError,
    PdfTextExtractor,
    RawDocument,
    UnsupportedDocumentType,
    extract_text,
    extract_text_with_pages,
)
from stock_research.facts import FinancialFactExtractor
from stock_research.market_data import MarketDataError, YahooFinanceProvider


class _Response:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class DataLayerTests(unittest.TestCase):
    def test_document_chunks_keep_line_locations_and_hash(self) -> None:
        document = RawDocument(
            company_id=uuid4(),
            source_type="company_ir",
            source_url="https://example.test/report.txt",
            title="Sample report",
            content="Revenue increased.\n\nCash flow remained positive.",
        )
        chunks = DocumentIngestor(max_chars=100).chunk(document)
        self.assertEqual(len(chunks), 1)
        self.assertEqual((chunks[0].start_line, chunks[0].end_line), (1, 3))
        self.assertEqual(len(document.content_hash), 64)

    def test_document_chunks_preserve_pdf_page_boundaries(self) -> None:
        document = RawDocument(
            company_id=uuid4(), source_type="hkex_filing", source_url="https://www1.hkexnews.hk/report.pdf",
            title="Report", content="Revenue 100\nCash flow 20\nRisk competition", page_starts=(1, 3),
        )
        chunks = DocumentIngestor(max_chars=100).chunk(document)
        self.assertEqual([chunk.page for chunk in chunks], [1, 2])

    def test_yahoo_adapter_parses_chart_response(self) -> None:
        payload = {
            "chart": {
                "result": [{
                    "timestamp": [1754006400, 1754092800],
                    "indicators": {"quote": [{
                        "open": [100, 101], "high": [103, 104], "low": [99, 100],
                        "close": [102, 103], "volume": [1000, 1200]
                    }]}
                }],
                "error": None,
            }
        }
        provider = YahooFinanceProvider(opener=lambda *_args, **_kwargs: _Response(payload))
        bars = provider.get_daily_bars("0700.HK", date(2025, 8, 1), date(2025, 8, 3))
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].symbol, "0700.HK")
        self.assertEqual(bars[0].close, 102.0)
        self.assertEqual(bars[0].provider, "yahoo_chart")

    def test_yahoo_adapter_surfaces_empty_result(self) -> None:
        payload = {"chart": {"result": [None], "error": {"description": "Unknown symbol"}}}
        provider = YahooFinanceProvider(opener=lambda *_args, **_kwargs: _Response(payload))
        with self.assertRaises(MarketDataError):
            provider.get_daily_bars("invalid", date(2025, 8, 1), date(2025, 8, 3))

    def test_html_text_extraction_removes_scripts_and_normalizes_lines(self) -> None:
        fetched = FetchedDocument(
            url="https://www1.hkexnews.hk/sample.html",
            content_type="text/html",
            body=b"<html><body><h1>Results</h1><script>ignore()</script><p>Revenue 100</p></body></html>",
            fetched_at=datetime.now(timezone.utc),
        )
        self.assertEqual(extract_text(fetched), "Results\nRevenue 100")

    def test_pdf_is_explicitly_unsupported_until_parser_is_configured(self) -> None:
        fetched = FetchedDocument(
            url="https://www1.hkexnews.hk/sample.pdf",
            content_type="application/pdf",
            body=b"%PDF-1.7",
            fetched_at=datetime.now(timezone.utc),
        )
        with self.assertRaises(UnsupportedDocumentType):
            extract_text(fetched)

    def test_extract_text_with_pages_routes_pdf_and_html(self) -> None:
        """PDF 走 pdftotext 保留页边界（行号从 1 累计）；HTML 走可见文本无页边界。"""
        pdf = FetchedDocument(
            url="https://www1.hkexnews.hk/results.pdf",
            content_type="application/pdf",
            body=b"%PDF-1.7 fake",
            fetched_at=datetime.now(timezone.utc),
        )
        runner = lambda _body, _timeout: (0, b"Revenue 100\nProfit 20\fRisk remains", b"")
        text, page_starts = extract_text_with_pages(pdf, extractor=PdfTextExtractor(runner=runner))
        self.assertEqual(text, "Revenue 100\nProfit 20\nRisk remains")
        # 第 1 页占 2 行，第 2 页从第 3 行开始
        self.assertEqual(page_starts, (1, 3))

        html = FetchedDocument(
            url="https://ir.example.com/note.html",
            content_type="text/html",
            body=b"<html><body><p>Revenue 100</p></body></html>",
            fetched_at=datetime.now(timezone.utc),
        )
        text, page_starts = extract_text_with_pages(html)
        self.assertEqual(text, "Revenue 100")
        self.assertEqual(page_starts, ())

    def test_extract_text_with_pages_sniffs_pdf_by_body_header(self) -> None:
        """content_type 误标（如 application/octet-stream）时按 %PDF 头嗅探。"""
        fetched = FetchedDocument(
            url="https://www1.hkexnews.hk/mislabeled.pdf",
            content_type="application/octet-stream",
            body=b"%PDF-1.7 mislabeled",
            fetched_at=datetime.now(timezone.utc),
        )
        runner = lambda _body, _timeout: (0, b"page one", b"")
        text, page_starts = extract_text_with_pages(fetched, extractor=PdfTextExtractor(runner=runner))
        self.assertEqual(text, "page one")
        self.assertEqual(page_starts, (1,))

    def test_pdf_extractor_preserves_page_numbers_and_chunks(self) -> None:
        runner = lambda _body, _timeout: (0, b"Revenue 100\nCash flow positive\fRisk remains", b"")
        extractor = PdfTextExtractor(runner=runner)
        chunks = extractor.extract_chunks(uuid4(), b"%PDF-1.7 fake", max_chars=100)
        self.assertEqual([chunk.page for chunk in chunks], [1, 2])
        self.assertIn("Revenue 100", chunks[0].text)

    def test_pdf_extractor_rejects_parser_failure(self) -> None:
        runner = lambda _body, _timeout: (1, b"", b"bad xref")
        with self.assertRaises(PdfParserError):
            PdfTextExtractor(runner=runner).extract_pages(b"%PDF-1.7 fake")

    def test_fetcher_enforces_https_allowlist_and_size(self) -> None:
        with self.assertRaises(DocumentFetchError):
            HttpDocumentFetcher().fetch("http://www1.hkexnews.hk/report.html")
        with self.assertRaises(DocumentFetchError):
            HttpDocumentFetcher().fetch("https://example.com/report.html")

    def test_fact_extractor_returns_cited_fact_with_normalized_amount(self) -> None:
        document = RawDocument(
            company_id=uuid4(),
            source_type="company_ir",
            source_url="https://example.test/report.txt",
            title="Sample report",
            content="Revenue FY2024 HK$ 12.5 billion",
        )
        chunk = DocumentIngestor(max_chars=100).chunk(document)[0]
        facts = FinancialFactExtractor().extract([chunk])
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].metric, "revenue")
        self.assertEqual(facts[0].value, 12_500_000_000)
        self.assertEqual(facts[0].currency, "HKD")
        self.assertEqual(facts[0].period_end, date(2024, 12, 31))
        self.assertEqual(facts[0].evidence_id, chunk.id)

    def test_fact_extractor_skips_ambiguous_multi_year_table_row(self) -> None:
        document = RawDocument(
            company_id=uuid4(),
            source_type="company_ir",
            source_url="https://example.test/report.txt",
            title="Sample report",
            content="Revenue 2024 100 2023 90",
        )
        chunk = DocumentIngestor(max_chars=100).chunk(document)[0]
        self.assertEqual(FinancialFactExtractor().extract([chunk]), [])



if __name__ == "__main__":
    unittest.main()
