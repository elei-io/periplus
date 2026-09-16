import json
import struct
import unittest

import httpx
from worker_probe import encode, json_rows, varint


class WireTests(unittest.TestCase):
    def test_json_lines_preserve_unicode_line_separators(self):
        value = {"text": "a\u2028b\u0085c\n😀"}
        response = httpx.Response(
            200, content=(json.dumps(value, ensure_ascii=False) + "\n").encode()
        )
        self.assertEqual(list(json_rows(response)), [value])

    def test_full_text_slices_character_offsets_before_utf8_encoding(self):
        doc = {"document_id": "a" * 64, "sample_rank": 1, "document_text": "a😀bé"}
        element = {
            "node_index": 7,
            "parent_index": None,
            "subtree_end_index": 8,
            "sibling_index": 0,
            "depth": 0,
            "tag": "p",
            "namespace": None,
            "attributes": {"id": "é"},
            "text_direct": "😀b",
            "text_start": 1,
            "text_end": 3,
        }
        value = encode(doc, element)
        self.assertTrue(
            value.startswith(b"a" * 64 + struct.pack("<II", 1, 7) + b"\x01")
        )
        self.assertTrue(
            value.endswith(struct.pack("<QQ", 1, 3) + b"\x05" + "😀b".encode())
        )

    def test_varint_multibyte_lengths(self):
        self.assertEqual(varint(127), b"\x7f")
        self.assertEqual(varint(128), b"\x80\x01")
        self.assertEqual(varint(16384), b"\x80\x80\x01")
