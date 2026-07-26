-- atlas:description=Reports whether a text value contains non-whitespace content.
CREATE OR REPLACE MACRO has_text(value) AS (
    coalesce(regexp_matches(value, '[^ \t\r\n\f]'), false)
);
