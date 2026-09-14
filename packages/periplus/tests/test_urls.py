import unittest

from periplus.urls import normalize_url


class NormalizeUrlTests(unittest.TestCase):
    def test_rejects_malformed_authorities_without_repairing_them(self):
        for host in (' www.ardian.com', 'example .com', 'exa\tmple.com', 'exa\nmple.com',
                     'example..com', '-example.com', 'example-.com', '%20example.com',
                     'example.com\\evil', 'a' * 64 + '.com'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                normalize_url('https://' + host + '/')

    def test_accepts_international_names_and_ip_literals(self):
        for url in ('https://例え.jp/', 'https://xn--r8jz45g.jp/', 'https://example.com./',
                    'https://1.1.1.1/', 'https://[2606:4700:4700::1111]/'):
            self.assertEqual(normalize_url(url), url)

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
