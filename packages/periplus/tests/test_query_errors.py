import unittest
from periplus.platform.clickhouse import ClickHouseError
from periplus.query.errors import query_error


class QueryErrorTests(unittest.TestCase):
    def test_clickhouse_failures_are_classified_without_exposing_server_messages(self):
        for code,status,kind in [('159',408,'resource_limit'),('241',422,'resource_limit'),('307',422,'resource_limit'),('62',422,'sql_invalid'),('transport',503,'storage_unavailable')]:
            with self.subTest(code=code):
                actual,error=query_error(ClickHouseError('private-query-id',code=code))
                self.assertEqual((actual,error.code),(status,kind))
                self.assertNotIn('private-query-id',error.detail)
