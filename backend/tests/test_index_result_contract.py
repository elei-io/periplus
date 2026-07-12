from unittest import TestCase
from unittest.mock import patch

from actions.index.schemas import IndexLink
from actions.index.service import _summarize_links


class IndexResultContractTests(TestCase):
    def test_large_result_is_reduced_to_aggregates_and_bounded_sample(self) -> None:
        links = [
            IndexLink(
                source_url="https://example.com",
                url=f"https://{'example.com' if index < 80 else 'outside.test'}/{index}",
                text=f"Link {index}",
                title="",
                depth=1,
                link_index=index,
                internal=index < 80,
            )
            for index in range(100)
        ]

        with patch("actions.index.service.get_int", return_value=20):
            summary = _summarize_links(links, sample_seed="https://example.com")

        self.assertEqual(summary[0], 100)
        self.assertEqual(summary[1], 80)
        self.assertEqual(len(summary[2]), 20)
        self.assertEqual(
            summary[2],
            _summarize_links(links, sample_seed="https://example.com")[2],
        )
