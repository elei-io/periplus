"""A document search row without duplicating the element array beside flat rows."""

from client import require, target
from experiment import run_cases


def run() -> None:
    relation = target("documents_text")
    require(
        f"""CREATE TABLE {relation} (
        document_id FixedString(64),sample_rank UInt32,
        document_text String CODEC(ZSTD(3)),element_count UInt32,
        INDEX words lower(document_text) TYPE text(tokenizer=splitByNonAlpha)
    ) ENGINE=MergeTree ORDER BY document_id SETTINGS min_bytes_for_wide_part=268435456""",
        read=False,
    )
    require(
        f"INSERT INTO {relation} SELECT lower(hex(document_id)),sample_rank,document_text,length(elements) FROM {target('source_docs')} SETTINGS max_block_size=16,min_insert_block_size_bytes=33554432,min_insert_block_size_rows=0",
        label="load:documents_text:0:20000",
        read=False,
        seconds=600,
    )
    run_cases(
        20000,
        "documents_text",
        {
            "document_word_rare": f"SELECT document_id FROM {relation} WHERE hasAllTokens(lower(document_text),['ic3','scammers']) ORDER BY document_id",
            "document_word_common": f"SELECT count() AS n FROM {relation} WHERE hasToken(lower(document_text),'the')",
            "element_count": f"SELECT sum(element_count) AS n FROM {relation}",
        },
        2,
    )


if __name__ == "__main__":
    run()
