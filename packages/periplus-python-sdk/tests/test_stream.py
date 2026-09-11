import json
import unittest
from unittest.mock import patch

import httpx

from periplus_sdk import Client, ResponseError, ApiError
from test_client import RESULT, stream_response


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.read = 0
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            self.read += 1
            yield chunk

    def close(self):
        self.closed = True


class StreamTests(unittest.TestCase):
    def client(self, response):
        factory = httpx.Client
        with patch('periplus_sdk.client.httpx.Client', side_effect=lambda **kw:
                   factory(**kw, transport=httpx.MockTransport(lambda r: response))):
            client = Client('https://public.example')
        self.addCleanup(client.close)
        return client

    def test_incremental_consumption_and_early_close(self):
        response = stream_response(dict(RESULT, truncated=False))
        chunks = Chunks(response.content.splitlines(keepends=True))
        client = self.client(httpx.Response(200, headers=response.headers, stream=chunks))
        with client.stream('SELECT 1') as stream:
            self.assertEqual(chunks.read, 1)
            self.assertEqual(next(stream), RESULT['rows'])
            self.assertEqual(chunks.read, 2)
            self.assertFalse(stream.result.complete)
        self.assertTrue(chunks.closed)
        self.assertEqual(chunks.read, 2)

    def test_eof_error_and_truncation_are_not_complete_data(self):
        for response, error in [
            (stream_response(dict(RESULT, truncated=False), terminal=False), ResponseError),
            (stream_response(RESULT), ApiError),
        ]:
            with self.subTest(error=error):
                with self.client(response).stream('SELECT 1') as stream:
                    self.assertEqual(next(stream), RESULT['rows'])
                    with self.assertRaises(error):
                        next(stream)
                    with self.assertRaises(ResponseError):
                        next(stream)

    def test_partial_results_require_opt_in(self):
        with self.client(stream_response(RESULT)).stream('SELECT 1', allow_partial=True) as stream:
            self.assertEqual(list(stream), [RESULT['rows']])
            self.assertTrue(stream.result.complete)
            self.assertTrue(stream.result.truncated)
            self.assertEqual(stream.result.row_count, 1)

    def test_missing_metadata_bad_counts_and_error_frames(self):
        good = stream_response(dict(RESULT, truncated=False)).content.decode().splitlines()
        variants = [
            ([good[1]], ResponseError),
            ([good[0], good[1], json.dumps(dict(type='complete', row_count=99, result_bytes=1,
                                               elapsed_ms=1, truncated=False, truncation_reason=None))], ResponseError),
            ([good[0], json.dumps(dict(type='error', status=408, code='resource_limit', detail='Deadline reached'))], ApiError),
        ]
        for frames, error in variants:
            with self.subTest(frames=frames), self.assertRaises(error):
                with self.client(httpx.Response(200, headers={'content-type':'application/x-ndjson'},
                                                content='\n'.join(frames)+'\n')).stream('SELECT 1') as stream:
                    list(stream)
