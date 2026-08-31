import tempfile
import unittest
from pathlib import Path

from deepsearch.domain.models import SearchResult, Source
from deepsearch.infrastructure.cache import ResearchCache
from deepsearch.domain.ranking import SourceRanker


class CacheAndRankingTests(unittest.TestCase):
    def test_search_and_source_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ResearchCache(Path(directory), ttl_seconds=3600)
            results = [SearchResult("Python", "https://python.org/", "official", "python", "test")]
            self.assertIsNone(cache.get_search("test", "python", 3))
            cache.set_search("test", "python", 3, results)
            self.assertEqual(cache.get_search("test", "python", 3)[0].title, "Python")
            source = Source("Python", "https://python.org/", "official content", provider="test")
            cache.set_source(source)
            self.assertEqual(cache.get_source(source.url).content, "official content")
            self.assertEqual(cache.clear(), 2)

    def test_ranking_prefers_relevant_and_diverse_domains(self):
        ranker = SourceRanker(per_domain_limit=1)
        results = [
            SearchResult("Python docs", "https://docs.python.org/a", "Python 3.13 features"),
            SearchResult("More Python", "https://docs.python.org/b", "Python details"),
            SearchResult("Python release", "https://python.org/release", "Python 3.13 release"),
        ]
        ranked = ranker.rank(results, "Python 3.13 features", ["release"], limit=2)
        domains = {ranker.domain(item.url) for item in ranked}
        self.assertEqual(len(ranked), 2)
        self.assertEqual(len(domains), 2)
        self.assertGreaterEqual(ranked[0].rank_score, ranked[-1].rank_score)


if __name__ == "__main__":
    unittest.main()
