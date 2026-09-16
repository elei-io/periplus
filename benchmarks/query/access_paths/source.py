"""Snapshot loaders for immutable retained inputs into the isolated benchmark."""

from client import target
from layouts import captures
from load import SOURCE


def capture_source_ddl() -> str:
    return captures("source_captures", "none")


def capture_source_insert() -> str:
    return f"""INSERT INTO {target("source_captures")} (capture_id,document_id,sample_rank,url,captured_at,links)
    WITH cutFragment(trimBoth(coalesce(nullIf(c.effective_url,''),c.requested_url))) AS observed_url,
    extract(observed_url,'^[Hh][Tt][Tt][Pp][Ss]?://[^/?#]+') AS authority,
    replaceRegexpOne(lower(authority),'^(http://.+):80$|^(https://.+):443$','\\\\1\\\\2') AS normalized_authority,
    substring(observed_url,length(authority)+1) AS suffix
    SELECT c.capture_id,lower(hex(c.document_id)),i.sample_rank,
    concat(normalized_authority,if(suffix='' OR startsWith(suffix,'?'),'/',''),suffix),c.captured_at,c.links
    FROM {SOURCE}.captures c INNER JOIN {target("source_ids")} i USING(document_id)
    WHERE c.completeness='complete' AND c.document_id IN (SELECT document_id FROM {target("source_ids")})
    SETTINGS max_block_size=256,min_insert_block_size_bytes=33554432,min_insert_block_size_rows=0"""


ANCHOR = "89ee04017fb94c65660b85397c11135e9179c6f12cef0c774c01bb020a170151"


def resume_documents() -> None:
    """Resume only missing committed identities after an uncertain INSERT response."""
    from client import data, require

    upper = data(
        f"SELECT hex(max(document_id)) AS v FROM {target('source_ids')} WHERE sample_rank>1"
    )[0]["v"]
    require(
        f"""INSERT INTO {target("source_docs")} SELECT d.*,i.sample_rank
        FROM {SOURCE}.html_documents d INNER JOIN {target("source_ids")} i USING(document_id)
        WHERE (d.document_id<=unhex('{upper}') OR d.document_id=unhex('{ANCHOR}'))
        AND d.document_id NOT IN (SELECT document_id FROM {target("source_docs")})
        SETTINGS max_block_size=4,min_insert_block_size_bytes=33554432,min_insert_block_size_rows=0""",
        label="source:documents",
        read=False,
        seconds=900,
    )


def resume_captures() -> None:
    from client import require

    sql = capture_source_insert().replace(
        "    SETTINGS ",
        f" AND c.capture_id NOT IN (SELECT capture_id FROM {target('source_captures')}) SETTINGS ",
    )
    require(sql, label="source:captures", read=False, seconds=300)


def verify() -> dict:
    from client import data

    documents = data(
        f"SELECT count() n,uniqExact(document_id) ids FROM {target('source_docs')}"
    )[0]
    expected = data(
        f"SELECT count() n,uniqExact(document_id) ids FROM {target('source_ids')}"
    )[0]
    assert documents == expected and documents["n"] > 0, (documents, expected)
    missing = data(
        f"SELECT count() n FROM {target('source_ids')} WHERE document_id NOT IN (SELECT document_id FROM {target('source_docs')})"
    )[0]["n"]
    assert missing == 0
    captures = data(
        f"SELECT count() n,uniqExact(capture_id) ids FROM {target('source_captures')}"
    )[0]
    assert captures["n"] == captures["ids"]
    uncovered = data(
        f"SELECT count() n FROM {target('source_docs')} WHERE lower(hex(document_id)) NOT IN (SELECT document_id FROM {target('source_captures')})"
    )[0]["n"]
    assert uncovered == 0, "Sample includes documents without a complete capture"
    return {
        "documents": documents,
        "captures": captures,
        "uncovered_documents": uncovered,
    }
