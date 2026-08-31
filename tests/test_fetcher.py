import unittest

from deepsearch.infrastructure.fetcher import WebFetcher
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


if __name__ == "__main__":
    unittest.main()
