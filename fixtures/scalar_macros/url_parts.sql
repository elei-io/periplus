-- atlas:description=Parses a URL into normalized structural components.
CREATE OR REPLACE MACRO url_parts(value) AS (
    WITH parts AS (
        SELECT
            lower(
                regexp_extract(value, '^([A-Za-z][A-Za-z0-9+.-]*):', 1)
            ) AS scheme,
            regexp_extract(
                value,
                '^[A-Za-z][A-Za-z0-9+.-]*://([^/?#]*)',
                1
            ) AS authority,
            regexp_extract(
                value,
                '^[A-Za-z][A-Za-z0-9+.-]*://[^/?#]*([^?#]*)',
                1
            ) AS path,
            CASE
                WHEN strpos(value, '?') > 0
                THEN substring(
                    value
                    FROM strpos(value, '?') + 1
                    FOR CASE
                        WHEN strpos(value, '#') > strpos(value, '?')
                        THEN strpos(value, '#') - strpos(value, '?') - 1
                        ELSE length(value)
                    END
                )
                ELSE NULL
            END AS query,
            CASE
                WHEN strpos(value, '#') > 0
                THEN substring(value FROM strpos(value, '#') + 1)
                ELSE NULL
            END AS fragment
    ),
    parsed AS (
        SELECT
            *,
            CASE
                WHEN starts_with(authority, '[')
                THEN lower(regexp_extract(authority, '^\[([^\]]+)\]', 1))
                ELSE lower(regexp_extract(authority, '^([^:]+)', 1))
            END AS host,
            CASE
                WHEN starts_with(authority, '[')
                THEN regexp_extract(authority, '\]:([0-9]+)$', 1)
                ELSE regexp_extract(authority, ':([0-9]+)$', 1)
            END AS explicit_port
        FROM parts
    )
    SELECT struct_pack(
        scheme := scheme,
        host := host,
        port := CASE
            WHEN explicit_port <> '' THEN cast(explicit_port AS INTEGER)
            WHEN scheme = 'http' THEN 80
            WHEN scheme = 'https' THEN 443
            ELSE 0
        END,
        path := CASE WHEN path = '' THEN '/' ELSE path END,
        query := query,
        fragment := fragment
    )
    FROM parsed
);
