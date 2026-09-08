from pathlib import Path
import re
import unittest

from periplus.materialization.registry import PROJECTIONS


class AppendOnlyCatalogueTests(unittest.TestCase):
    def test_source_contains_no_ingest_or_semantic_material_replacement_dml(
        self,
    ) -> None:
        source_root = Path(__file__).parents[1] / "src" / "periplus"
        material_tables = "|".join(
            re.escape(spec.name) for spec in PROJECTIONS
        )
        prohibited = re.compile(
            r"\b(?:MERGE\s+INTO|UPDATE|DELETE\s+FROM)\s+"
            r'(?:"?[A-Za-z_][A-Za-z0-9_]*"?\.)?'
            r"(?:"
            r'"?ingest"?\."?[A-Za-z_][A-Za-z0-9_]*"?'
            r'|"?material"?\."?'
            rf"(?:{material_tables})"
            r'"?)',
            re.IGNORECASE,
        )
        violations: list[str] = []
        for path in sorted(source_root.rglob("*")):
            if path.suffix not in {".py", ".sql"}:
                continue
            source = path.read_text(encoding="utf-8")
            if path == source_root / "retention" / "catalogue.py":
                # Retirement may delete evidence, never revise it in place.
                source = re.sub(r"\bDELETE\s+FROM\b", "RETIRE FROM", source, flags=re.IGNORECASE)
            match = prohibited.search(source)
            if match is not None:
                violations.append(
                    f"{path.relative_to(source_root)}: {match.group(0)}"
                )
        self.assertEqual(violations, [])

    def test_materialization_never_deletes_parquet_files(self) -> None:
        materialization_root = (
            Path(__file__).parents[1] / "src" / "periplus" / "materialization"
        )
        violations: list[str] = []
        for path in sorted(materialization_root.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if (
                "unlink(" in source
                or "rmtree(" in source
                or "remove(" in source
            ):
                violations.append(path.name)
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
