"""Native optimizer controls, result-size pressure, and cache bypass cases."""

from client import BYPASS_CACHES, data, literal, target
from experiment import run_cases
from queries import document_cases, element_cases


def run() -> None:
    worst = data(
        f"SELECT lower(hex(document_id)) id FROM {target('source_docs')} WHERE sample_rank=162"
    )[0]["id"]
    for layout in ["elements_lean", "elements_full_lean"]:
        original = element_cases(layout, layout.startswith("elements_full"))
        run_cases(
            20000,
            layout,
            {
                "class_map_subcolumns_off": original["class_element_expression"]
                + " SETTINGS optimize_functions_to_subcolumns=0",
                "class_map_subcolumns_off_bypass": original["class_element_expression"]
                + " SETTINGS optimize_functions_to_subcolumns=0,"
                + BYPASS_CACHES,
                "common_text_first_1000": f"SELECT document_id,node_index,text_direct FROM {target(layout)} WHERE hasToken(lower(text_direct),'the') ORDER BY document_id,node_index LIMIT 1000",
                "common_text_unbounded": f"SELECT document_id,node_index,text_direct FROM {target(layout)} WHERE hasToken(lower(text_direct),'the')",
            },
            2,
        )
    for layout in ["docs_plain", "docs_indexed_packed", "elements_full_lean"]:
        original = (
            document_cases(layout)
            if layout.startswith("docs")
            else element_cases(layout, True)
        )
        preview = original["full_preview"].replace(
            " LIMIT 10", f" WHERE document_id={literal(worst)} LIMIT 10"
        )
        run_cases(20000, layout, {"worst_document_preview": preview}, 2)
    for layout, case in [
        ("docs_indexed_packed", "document_word_rare"),
        ("elements_compact", "class_element"),
        ("elements_full_lean", "subtree_word_rare"),
    ]:
        original = (
            document_cases(layout)
            if layout.startswith("docs")
            else element_cases(layout, layout.startswith("elements_full"))
        )
        run_cases(
            20000,
            layout,
            {case + "_bypass": original[case] + " SETTINGS " + BYPASS_CACHES},
            2,
        )


if __name__ == "__main__":
    run()
