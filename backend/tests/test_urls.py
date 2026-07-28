import unittest

from atlas.urls import normalize_url


class NormalizeUrlTests(unittest.TestCase):
    def test_preserves_opaque_query_bytes_and_order(self) -> None:
        value = (
            "HTTPS://WWW.BING.COM:443/ck/a?!&&p=signed"
            "&ptn=3&u=encoded_destination#fragment"
        )

        self.assertEqual(
            normalize_url(value),
            (
                "https://www.bing.com/ck/a?!&&p=signed"
                "&ptn=3&u=encoded_destination"
            ),
        )

    def test_preserves_parameters_that_look_like_tracking_data(self) -> None:
        self.assertEqual(
            normalize_url(
                "https://example.com/path?utm_source=mail&b=2&a=1"
            ),
            "https://example.com/path?utm_source=mail&b=2&a=1",
        )

    def test_normalizes_only_safe_url_components(self) -> None:
        self.assertEqual(
            normalize_url("HTTP://EXAMPLE.COM:80#section"),
            "http://example.com/",
        )


if __name__ == "__main__":
    unittest.main()
