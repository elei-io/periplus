# /// script
# requires-python = ">=3.11"
# dependencies = ["periplus-python-sdk[notebook]>=0.7.0"]
# ///
"""Run with: uv run marimo edit packages/periplus-python-sdk/examples/notebook.py"""
import marimo

__generated_with = "0.24.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    from periplus_sdk import sql_api
    return mo, sql_api


@app.cell
def _(sql_api):
    pp = sql_api.create_engine("https://periplus.dev", mode="stable")
    return (pp,)


@app.cell
def _(mo):
    mo.md("""
    Select **pp** in SQL cells. Expand **pp → periplus → public_v1** in Data Sources
    to browse views and columns. Queries use the public service's
    execution and result limits.
    """)
    return


@app.cell
def _(mo, pp):
    captures = mo.sql(
        """
        SELECT capture_id, requested_url, content_id
        FROM public_v1.capture
        LIMIT 10
        """,
        engine=pp,
    )
    return (captures,)


@app.cell
def _(captures, pp, sql_api):
    # Replace this Python predicate with your own qualification logic.
    _ids = [row["content_id"] for row in captures.to_dicts() if row["requested_url"].startswith("https://")]
    selected = sql_api.bind(pp, content_ids=_ids)
    return (selected,)


@app.cell
def _(mo, selected):
    elements = mo.sql(
        """
        SELECT content_id, node_index, parent_index, tag,
               trim(text_direct) AS text, attributes['class'] AS elem_class
        FROM public_v1.html_element
        WHERE content_id IN (SELECT unnest(CAST(:content_ids AS VARCHAR[])))
          AND text_direct IS NOT NULL AND trim(text_direct) <> ''
        LIMIT 100
        """,
        engine=selected,
    )
    return (elements,)


if __name__ == "__main__":
    app.run()

