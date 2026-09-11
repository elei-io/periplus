# /// script
# requires-python = ">=3.11"
# dependencies = ["periplus-python-sdk[notebook]>=0.6.0"]
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
    Select **pp** in SQL cells. Expand **pp → public_v1** in Data Sources
    to browse views and columns. Queries use the public service's
    execution and result limits.
    """)
    return


@app.cell
def _(mo, pp):
    captures = mo.sql(
        f"""
        SELECT capture_id, requested_url
        FROM public_v1.capture
        LIMIT 10
        """,
        engine=pp,
    )
    return (captures,)


if __name__ == "__main__":
    app.run()

