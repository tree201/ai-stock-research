from unittest import TestCase

from stock_research.web_search import DuckDuckGoSearch, GoogleNewsSearch, _decode_redirect


SAMPLE_HTML = (
    '<div class="result results_links results_links_deep web-result">'
    '<div class="links_main links_deep result__body">'
    '<h2 class="result__title">'
    '<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ffinance.yahoo.com%2Fquote%2F0001.HK%2F&amp;rut=abc">'
    "CK Hutchison Holdings (0001.HK)</a>"
    "</h2>"
    '<a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ffinance.yahoo.com%2Fquote%2F0001.HK%2F&amp;rut=abc">'
    "Find the latest CK Hutchison <b>Holdings</b> stock quote.</a>"
    "</div></div>"
)


class WebSearchTests(TestCase):
    def test_parse_extracts_title_url_and_snippet(self) -> None:
        results = DuckDuckGoSearch._parse(SAMPLE_HTML)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "CK Hutchison Holdings (0001.HK)")
        self.assertEqual(results[0].url, "https://finance.yahoo.com/quote/0001.HK/")
        self.assertIn("CK Hutchison Holdings", results[0].snippet)
        self.assertNotIn("<b>", results[0].snippet)

    def test_decode_redirect_resolves_uddg(self) -> None:
        self.assertEqual(
            _decode_redirect("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1&rut=x"),
            "https://example.com/a?b=1",
        )

    def test_decode_redirect_passes_plain_urls_through(self) -> None:
        self.assertEqual(
            _decode_redirect("https://example.com/plain"),
            "https://example.com/plain",
        )

    def test_search_returns_empty_on_network_failure(self) -> None:
        def failing(_url: str) -> str:
            raise OSError("boom")

        search = DuckDuckGoSearch(fetch=failing)
        self.assertEqual(search.search("CKH Holdings"), [])

    def test_search_returns_empty_for_blank_query(self) -> None:
        search = DuckDuckGoSearch()
        self.assertEqual(search.search("   "), [])


SAMPLE_RSS = (
    '<rss version="2.0"><channel>'
    "<item><title>长江和记向巴拿马索赔逾15亿美元</title>"
    "<link>https://news.google.com/rss/articles/abc</link>"
    "<source>腾讯新闻</source><description>描述</description>"
    "<pubDate>Thu, 03 Sep 2026 21:15:00 GMT</pubDate></item>"
    "</channel></rss>"
)


class GoogleNewsTests(TestCase):
    def test_parse_extracts_title_link_and_source(self) -> None:
        results = GoogleNewsSearch._parse(SAMPLE_RSS)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "长江和记向巴拿马索赔逾15亿美元")
        self.assertEqual(results[0].url, "https://news.google.com/rss/articles/abc")
        self.assertEqual(results[0].snippet, "腾讯新闻")
        self.assertEqual(results[0].published, "Thu, 03 Sep 2026 21:15:00 GMT")

    def test_parse_rejects_invalid_xml(self) -> None:
        self.assertEqual(GoogleNewsSearch._parse("<not-xml"), [])

    def test_search_returns_empty_on_network_failure(self) -> None:
        def failing(_request, **_kwargs):
            raise OSError("boom")

        search = GoogleNewsSearch(opener=failing)
        self.assertEqual(search.search("CKH"), [])

    def test_search_returns_empty_for_blank_query(self) -> None:
        search = GoogleNewsSearch()
        self.assertEqual(search.search("  "), [])
