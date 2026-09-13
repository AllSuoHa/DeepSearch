import unittest
from types import SimpleNamespace
from unittest.mock import patch

from deepsearch.infrastructure.fetcher import WebFetcher, _PublicRedirectHandler
from deepsearch.domain.models import SearchResult


class FetcherTests(unittest.TestCase):
    def test_mock_pages_are_fetched_concurrently_without_network(self):
        results = [
            SearchResult("a", "mock://a", "first source summary", provider="mock"),
            SearchResult("b", "mock://b", "second source summary", provider="mock"),
        ]
        sources = WebFetcher(workers=2).fetch_all(results)
        self.assertEqual(len(sources), 2)
        self.assertTrue(all(source.fetched for source in sources))
        self.assertIn("Mock", sources[0].content)

    def test_control_characters_are_removed(self):
        self.assertEqual(WebFetcher._sanitize("hello\x00world\x08!\n"), "helloworld!\n")

    def test_private_or_non_http_sources_are_rejected_before_network_access(self):
        fetcher = WebFetcher()
        blocked = (
            "http://127.0.0.1/admin",
            "http://127.1/admin",
            "http://192.168.1.2/report",
            "http://service.local/report",
            "file:///etc/passwd",
            "https://user:password@example.org/report",
        )

        with patch("urllib.request.OpenerDirector.open") as opener:
            sources = [
                fetcher.fetch(SearchResult("blocked", url, "summary", provider="test"))
                for url in blocked
            ]

        opener.assert_not_called()
        self.assertTrue(all(not source.fetched for source in sources))
        self.assertTrue(all(source.error for source in sources))
        self.assertIn("内嵌凭据", sources[-1].error)

    def test_redirects_to_private_addresses_are_rejected(self):
        handler = _PublicRedirectHandler()

        with self.assertRaisesRegex(ValueError, "拒绝访问"):
            handler.redirect_request(None, None, 302, "Found", {}, "http://localhost/private")

    def test_html_extraction_removes_navigation_and_language_switchers(self):
        document = """
        <html><body><header>Site menu</header><nav>中文 English 日本語</nav>
        <main><article><h1>Useful finding</h1><p>This is the substantive article body with enough detail for extraction.</p></article></main>
        <footer>Privacy Terms</footer></body></html>
        """
        extracted = WebFetcher._extract(document)
        self.assertIn("Useful finding", extracted)
        self.assertNotIn("Site menu", extracted)
        self.assertNotIn("日本語", extracted)
        self.assertNotIn("Privacy Terms", extracted)

    def test_public_pdf_text_is_extracted_without_bypassing_encryption(self):
        class Reader:
            is_encrypted = False
            pages = [SimpleNamespace(extract_text=lambda: "Open paper content")]

        with patch.dict("sys.modules", {"pypdf": SimpleNamespace(PdfReader=lambda _: Reader())}):
            self.assertEqual(WebFetcher._extract_pdf(b"pdf"), "Open paper content")


if __name__ == "__main__":
    unittest.main()
