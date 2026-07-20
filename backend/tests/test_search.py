from __future__ import annotations

import unittest

from api.routers.search import SearchCompileRequest, compile_search_request, search_types
from repository.catalogue.query import classify_select
from repository.catalogue.search import SEARCH_TYPES, SearchType, compile_search


class SearchRegistryTests(unittest.TestCase):
    def test_registry_exposes_the_initial_result_types_in_order(self) -> None:
        self.assertEqual(
            [item.type for item in search_types().items],
            [SearchType.COVERAGE, SearchType.PAGES, SearchType.PASSAGES],
        )

    def test_every_strategy_compiles_one_bounded_select(self) -> None:
        for search_type in SEARCH_TYPES:
            with self.subTest(search_type=search_type):
                sql = compile_search(search_type, "atlas", 25)
                classify_select(sql)
                self.assertIn("LIMIT 25", sql)

    def test_free_text_is_quoted_as_data(self) -> None:
        response = compile_search_request(
            SearchCompileRequest(
                query="Atlas' evidence); DROP TABLE documents; --",
                result_type=SearchType.PAGES,
                limit=10,
            )
        )

        self.assertIn("Atlas'' evidence); DROP TABLE documents; --", response.sql)
        classify_select(response.sql)
        self.assertEqual(response.investigation_sql, response.sql)
