CREATE VIEW views.json_ld_scripts AS
WITH scripts AS (
    SELECT
        document_id,
        element_index,
        row_number() OVER (
            PARTITION BY document_id
            ORDER BY element_index
        )::BIGINT AS script_ordinal,
        macros.text_content(document_id, element_index) AS json_text
    FROM elements
    WHERE tag = 'script'
      AND lower(
            trim(
                regexp_replace(
                    coalesce(macros.get_attribute(attributes, 'type'), ''),
                    '[ \t\r\n\f]+',
                    ' ',
                    'g'
                )
            )
          ) = 'application/ld+json'
)
SELECT
    document_id,
    element_index,
    script_ordinal,
    json_text,
    json_valid(json_text) AS is_valid,
    CASE
        WHEN json_valid(json_text)
        THEN cast(json_text AS JSON)
    END AS json_value,
    CASE
        WHEN json_valid(json_text)
        THEN json_type(cast(json_text AS JSON))
    END AS root_type
FROM scripts;
