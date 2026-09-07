import unittest
from periplus.crawl.control.collections.exclusions import UrlExclusion


class UrlExclusionTests(unittest.TestCase):
    def test_host_and_decoded_segment_boundaries_cover_both_schemes(self):
        rule = UrlExclusion(host="*.Example.com", path_prefix="/admin/")
        for url in ("https://example.com/admin", "http://sub.example.com/admin/a?q=x",
                    "https://sub.example.com/%61dmin%2Fa", "https://example.com/x/../admin/a"):
            self.assertTrue(rule.matches(url), url)
        for url in ("https://notexample.com/admin", "https://example.com/administrator",
                    "https://example.com/admin/../public", "https://example.com.evil/admin"):
            self.assertFalse(rule.matches(url), url)
        self.assertTrue(UrlExclusion(host="*", path_prefix="/").matches("https://other.example/a"))

    def test_ambiguous_patterns_are_rejected(self):
        for host in ("https://example.com", "user@example.com", "example.com:443", "exa*mple.com"):
            with self.assertRaises(ValueError):
                UrlExclusion(host=host)
        with self.assertRaises(ValueError):
            UrlExclusion(host="example.com", path_prefix="/admin?x=1")
