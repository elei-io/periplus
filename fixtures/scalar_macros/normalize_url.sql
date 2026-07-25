-- atlas:description=Normalizes a URL into Atlas canonical comparison form.
CREATE OR REPLACE MACRO normalize_url(value) AS (
    WITH
    source AS (
        SELECT trim(value) AS input
    ),
    defragmented AS (
        SELECT
            CASE
                WHEN strpos(input, '#') > 0
                THEN left(input, strpos(input, '#') - 1)
                ELSE input
            END AS url
        FROM source
    ),
    parts AS (
        SELECT
            url,
            lower(
                regexp_extract(url, '^([A-Za-z][A-Za-z0-9+.-]*):', 1)
            ) AS scheme,
            regexp_extract(
                url,
                '^[A-Za-z][A-Za-z0-9+.-]*://([^/?#]*)',
                1
            ) AS authority,
            regexp_extract(
                url,
                '^[A-Za-z][A-Za-z0-9+.-]*://[^/?#]*([^?#]*)',
                1
            ) AS path,
            CASE
                WHEN strpos(url, '?') > 0
                THEN substring(url FROM strpos(url, '?') + 1)
                ELSE ''
            END AS raw_query
        FROM defragmented
    ),
    authority_parts AS (
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
    ),
    normalized AS (
        SELECT
            url,
            scheme,
            authority,
            path,
            host,
            explicit_port,
            raw_query AS query
        FROM authority_parts
    )
    SELECT CASE
        WHEN scheme NOT IN ('http', 'https')
          OR host = ''
          OR strpos(authority, '@') > 0
          OR (
              starts_with(authority, '[')
              AND NOT regexp_full_match(
                  authority,
                  '\[[^\]]+\](?::[0-9]+)?'
              )
          )
          OR (
              NOT starts_with(authority, '[')
              AND NOT regexp_full_match(
                  authority,
                  '[^:]+(?::[0-9]+)?'
              )
          )
          OR (
              explicit_port <> ''
              AND NOT coalesce(
                  try_cast(explicit_port AS INTEGER) BETWEEN 0 AND 65535,
                  false
              )
          )
        THEN NULL
        ELSE
            scheme
            || '://'
            || CASE
                WHEN strpos(host, ':') > 0 THEN '[' || host || ']'
                ELSE host
            END
            || CASE
                WHEN explicit_port = ''
                  OR (scheme = 'http' AND explicit_port = '80')
                  OR (scheme = 'https' AND explicit_port = '443')
                THEN ''
                ELSE ':' || explicit_port
            END
            || CASE WHEN path = '' THEN '/' ELSE path END
            || CASE
                WHEN coalesce(query, '') = '' THEN ''
                ELSE '?' || query
            END
    END
    FROM normalized
);
