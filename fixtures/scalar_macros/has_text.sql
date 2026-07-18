CREATE OR REPLACE MACRO has_text(value) AS (
    coalesce(regexp_matches(value, '[^ \t\r\n\f]'), false)
);
