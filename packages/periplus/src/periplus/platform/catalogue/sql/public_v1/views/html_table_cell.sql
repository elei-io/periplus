-- Compute one grid per table after filtering grouping keys; no stored projection.
CREATE OR REPLACE VIEW public_v1.html_table_cell AS
WITH rows AS NOT MATERIALIZED (
    SELECT r.content_id, t.node_index AS table_node_index,
           r.node_index AS row_node_index,
           (row_number() OVER (PARTITION BY r.content_id, t.node_index ORDER BY r.node_index) - 1)::INTEGER AS row_index,
           (count(*) OVER (PARTITION BY r.content_id, t.node_index, r.parent_index)
            - row_number() OVER (PARTITION BY r.content_id, t.node_index, r.parent_index ORDER BY r.node_index) + 1)::INTEGER AS remaining_rows
    FROM public_v1.html_element r
    JOIN public_v1.html_element p ON p.content_id = r.content_id AND p.node_index = r.parent_index
    JOIN public_v1.html_element t ON t.content_id = r.content_id
      AND t.node_index = CASE WHEN p.tag = 'table' THEN p.node_index ELSE p.parent_index END
    WHERE r.tag = 'tr' AND r.namespace = 'http://www.w3.org/1999/xhtml'
      AND p.tag IN ('table', 'thead', 'tbody', 'tfoot')
      AND p.namespace = 'http://www.w3.org/1999/xhtml'
      AND t.tag = 'table' AND t.namespace = 'http://www.w3.org/1999/xhtml'
), source_cells AS NOT MATERIALIZED (
    SELECT r.content_id, r.table_node_index, c.node_index, c.subtree_end_index, r.row_node_index, r.row_index,
           least(r.remaining_rows, CASE WHEN span_r = 0 THEN r.remaining_rows
                 ELSE least(coalesce(span_r, 1), 65534) END)::INTEGER AS row_span,
           greatest(1, least(coalesce(span_c, 1), 1000))::INTEGER AS column_span,
           c.tag = 'th' AS is_header
    FROM rows r
    JOIN (
        SELECT *,
            try_cast(regexp_extract(attributes['rowspan'], '^[\t\n\f\r ]*\+?([0-9]+)', 1) AS DOUBLE) AS span_r,
            try_cast(regexp_extract(attributes['colspan'], '^[\t\n\f\r ]*\+?([0-9]+)', 1) AS DOUBLE) AS span_c
        FROM public_v1.html_element
    ) c ON c.content_id = r.content_id AND c.parent_index = r.row_node_index
    WHERE c.tag IN ('td', 'th') AND c.namespace = 'http://www.w3.org/1999/xhtml'
), grouped AS (
    SELECT content_id, table_node_index,
           CASE WHEN count(*) > 10000 THEN error('HTML table exceeds 10000 cells') ELSE
           list(struct_pack(node_index := node_index, row_node_index := row_node_index,
                row_index := row_index, row_span := row_span, column_span := column_span,
                is_header := is_header) ORDER BY row_index, node_index) END AS cells
    FROM source_cells GROUP BY content_id, table_node_index
), positioned AS (
    -- grid[x + 1] is the exclusive row where column x becomes free.
    -- The fold follows source cells, carrying occupied columns and source positions.
    -- Singleton transforms name the current cell and its next free column.

    SELECT content_id, table_node_index, unnest(list_reduce(
    list_transform(cells, c -> struct_pack(
        cell := c, grid := []::INTEGER[], row_index := -1, column_end := 0,
        output := []::STRUCT(node_index INTEGER, column_index INTEGER)[])),
    (state, item) -> list_transform([item.cell], c ->
        list_transform([
            list_first(list_filter(
                range(CASE WHEN state.row_index = c.row_index THEN state.column_end ELSE 0 END,
                      len(state.grid)::INTEGER + 1),
                x -> coalesce(state.grid[x + 1], 0) <= c.row_index))::INTEGER
        ], col -> struct_pack(
            cell := c,
            grid := CASE
                WHEN col + c.column_span > 2048 THEN error('HTML table exceeds 2048 columns')
                WHEN list_has_any(
                    list_transform(range(col, col + c.column_span),
                        x -> coalesce(state.grid[x + 1], 0) > c.row_index), [true])
                THEN error('HTML table has overlapping cells')
                ELSE list_transform(range(0, greatest(len(state.grid)::INTEGER, col + c.column_span)),
                    x -> CASE WHEN x >= col AND x < col + c.column_span
                              THEN c.row_index + c.row_span
                              ELSE coalesce(state.grid[x + 1], 0) END)
                END,
            row_index := c.row_index,
            column_end := col + c.column_span,
            output := list_append(state.output, struct_pack(node_index := c.node_index, column_index := col))
        ))[1]
    )[1],
    struct_pack(cell := NULL::STRUCT(node_index INTEGER, row_node_index INTEGER, row_index INTEGER, row_span INTEGER, column_span INTEGER, is_header BOOLEAN), grid := []::INTEGER[], row_index := -1, column_end := 0, output := []::STRUCT(node_index INTEGER, column_index INTEGER)[])
).output) AS position
    FROM grouped
)
SELECT c.content_id, c.table_node_index, c.row_node_index, c.node_index,
       c.row_index, p.position.column_index AS column_index, c.row_span, c.column_span, c.is_header,
       material.element_text_excluding(c.content_id, c.node_index, ['table']) AS text
FROM positioned p
JOIN source_cells c ON c.content_id=p.content_id AND c.table_node_index=p.table_node_index
 AND c.node_index=p.position.node_index;
