"""Bounded native element inserts, verified before the document completion marker.

Each row carries the digest of its complete canonical document projection;
this compresses across rows and detects conflicting attempts without storing
a different random 32-byte hash for every element. Exact content claims belong to MaterialStore. No deduplication window or merge
is needed for retries: existing node identities must have the expected digest.
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import struct
from typing import Any

from periplus.platform.clickhouse.client import ClickHouseClient, INSERT_TARGET_BYTES, MAX_INSERT_BYTES

COLUMNS = (
    'document_id', 'node_index', 'parent_index', 'subtree_end_index',
    'sibling_index', 'depth', 'tag', 'namespace', 'attributes', 'text_direct',
    'text_start', 'text_end', 'text', 'output_digest',
)
MAX_BLOCK_ROWS = 131072


def _varint(value: int) -> bytes:
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def _string(value: str) -> bytes:
    encoded = value.encode('utf-8')
    return _varint(len(encoded)) + encoded


@dataclass(frozen=True)
class ElementRow:
    document_id: str
    node_index: int
    digest: str
    wire: bytes


def encode_element(document: Mapping[str, Any], element: Mapping[str, Any]) -> ElementRow:
    """Offsets are Unicode characters, so slice before encoding to RowBinary."""
    parent, namespace = element['parent_index'], element['namespace']
    attributes = sorted(element['attributes'].items())
    wire = b''.join((
        _string(document['document_id']), struct.pack('<I', element['node_index']),
        b'\x01' if parent is None else b'\x00' + struct.pack('<I', parent),
        struct.pack('<III', element['subtree_end_index'], element['sibling_index'], element['depth']),
        _string(element['tag']), b'\x01' if namespace is None else b'\x00' + _string(namespace),
        _varint(len(attributes)),
        b''.join(_string(key) + _string(value) for key, value in attributes),
        _string(element['text_direct']), struct.pack('<QQ', element['text_start'], element['text_end']),
        _string(document['document_text'][element['text_start']:element['text_end']]),
    ))
    digest = bytes.fromhex(document['output_digest'])
    if len(wire) + len(digest) > MAX_INSERT_BYTES:
        raise ValueError(f"Element {document['document_id']}:{element['node_index']} exceeds the insert byte limit")
    return ElementRow(document['document_id'], element['node_index'], digest.hex(), wire + digest)


def _digests(client: ClickHouseClient, database: str, rows: Sequence[ElementRow], table: str) -> dict[tuple[str, int], str]:
    ranges: dict[str, tuple[int, int]] = {}
    for row in rows:
        lo, hi = ranges.get(row.document_id, (row.node_index, row.node_index))
        ranges[row.document_id] = min(lo, row.node_index), max(hi, row.node_index)
    clauses, parameters = [], {}
    for index, (identity, (lo, hi)) in enumerate(ranges.items()):
        clauses.append(f'(document_id={{id{index}:String}} AND node_index BETWEEN {lo} AND {hi})')
        parameters[f'id{index}'] = identity
    found = client.query(
        f'SELECT document_id AS identity, node_index, lower(hex(output_digest)) AS digest '
        f'FROM {database}.{table} WHERE ' + ' OR '.join(clauses),
        parameters=parameters, max_response_bytes=32 * 1024 * 1024,
    )['data']
    result = {(row['identity'], row['node_index']): row['digest'] for row in found}
    if len(result) != len(found):
        raise ValueError('Duplicate immutable element identity')
    return result


def _insert_block(client: ClickHouseClient, database: str, rows: Sequence[ElementRow], table: str, columns: Sequence[str]) -> None:
    expected = {(row.document_id, row.node_index): row.digest for row in rows}
    if len(expected) != len(rows):
        raise ValueError('Duplicate projected element identity')
    existing = _digests(client, database, rows, table)
    if any(expected.get(key) != digest for key, digest in existing.items()):
        raise ValueError('Conflicting material element output')
    wire = b''.join(row.wire for row in rows if (row.document_id, row.node_index) not in existing)
    if wire:
        client.execute(
            f"INSERT INTO {database}.{table} ({','.join(columns)}) FORMAT RowBinary",
            data=wire, max_request_bytes=MAX_INSERT_BYTES,
        )
    actual = _digests(client, database, rows, table)
    if actual != expected:
        raise RuntimeError('Material element block is incomplete after insert')


def insert_elements(client: ClickHouseClient, database: str, documents: Sequence[Mapping[str, Any]]) -> None:
    rows: list[ElementRow] = []
    size = 0
    for document in documents:
        for element in document['elements']:
            row = encode_element(document, element)
            if rows and (size + len(row.wire) > INSERT_TARGET_BYTES or len(rows) >= MAX_BLOCK_ROWS):
                _insert_block(client, database, rows, 'html_elements', COLUMNS)
                rows, size = [], 0
            rows.append(row)
            size += len(row.wire)
    if rows:
        _insert_block(client, database, rows, 'html_elements', COLUMNS)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f'Invalid JSON constant: {value}')


def insert_json_ld(client: ClickHouseClient, database: str, documents: Sequence[Mapping[str, Any]]) -> None:
    """Preserve valid JSON-LD scripts; typed handles describe top-level objects only."""
    rows: list[ElementRow] = []
    size = 0
    columns = ('document_id', 'node_index', 'json', 'types', 'name', 'output_digest')
    for document in documents:
        for element in document['elements']:
            if element['tag'] != 'script' or element['attributes'].get('type', '').strip().lower() != 'application/ld+json':
                continue
            raw = document['document_text'][element['text_start']:element['text_end']]
            try:
                value = json.loads(raw, parse_constant=_reject_json_constant)
            except (ValueError, RecursionError):
                continue
            types = value.get('@type', []) if isinstance(value, dict) else []
            types = [types] if isinstance(types, str) else types
            types = [item for item in types if isinstance(item, str)] if isinstance(types, list) else []
            name = value.get('name', '') if isinstance(value, dict) else ''
            name = name if isinstance(name, str) else ''
            wire = (_string(document['document_id']) + struct.pack('<I', element['node_index'])
                    + _string(raw) + _varint(len(types)) + b''.join(_string(item) for item in types) + _string(name))
            digest = bytes.fromhex(document['output_digest'])
            row = ElementRow(document['document_id'], element['node_index'], digest.hex(), wire + digest)
            if len(row.wire) > MAX_INSERT_BYTES:
                raise ValueError(f"JSON-LD element {row.document_id}:{row.node_index} exceeds the insert byte limit")
            if rows and (size + len(row.wire) > INSERT_TARGET_BYTES or len(rows) >= MAX_BLOCK_ROWS):
                _insert_block(client, database, rows, 'json_ld', columns)
                rows, size = [], 0
            rows.append(row)
            size += len(row.wire)
    if rows:
        _insert_block(client, database, rows, 'json_ld', columns)
