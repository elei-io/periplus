-- atlas:description=Resolves a relative or absolute URL value against its captured source URL.
CREATE OR REPLACE MACRO resolve_url(source, href) AS (
    WITH RECURSIVE
    parts AS (
        SELECT
            source AS base,
            href AS reference,
            regexp_extract(source, '^([A-Za-z][A-Za-z0-9+.-]*):', 1) AS base_scheme,
            regexp_extract(
                source,
                '^[A-Za-z][A-Za-z0-9+.-]*://([^/?#]*)',
                1
            ) AS base_authority,
            regexp_extract(
                source,
                '^[A-Za-z][A-Za-z0-9+.-]*://[^/?#]*([^?#]*)',
                1
            ) AS base_path,
            CASE
                WHEN strpos(source, '?') > 0 THEN substring(
                    source
                    FROM strpos(source, '?')
                    FOR CASE
                        WHEN strpos(source, '#') > strpos(source, '?')
                            THEN strpos(source, '#') - strpos(source, '?')
                        ELSE length(source)
                    END
                )
                ELSE ''
            END AS base_query,
            CASE
                WHEN strpos(href, '#') > 0 THEN left(href, strpos(href, '#') - 1)
                ELSE href
            END AS reference_without_fragment,
            CASE
                WHEN strpos(href, '#') > 0 THEN substring(href FROM strpos(href, '#'))
                ELSE ''
            END AS fragment
    ),
    reference_parts AS (
        SELECT
            *,
            CASE
                WHEN strpos(reference_without_fragment, '?') > 0
                    THEN left(
                        reference_without_fragment,
                        strpos(reference_without_fragment, '?') - 1
                    )
                ELSE reference_without_fragment
            END AS raw_reference_path,
            CASE
                WHEN strpos(reference_without_fragment, '?') > 0
                    THEN substring(
                        reference_without_fragment
                        FROM strpos(reference_without_fragment, '?')
                    )
                ELSE ''
            END AS reference_query
        FROM parts
    ),
    resolved_reference AS (
        SELECT
            *,
            CASE
                WHEN starts_with(
                    lower(raw_reference_path),
                    lower(base_scheme) || ':'
                ) AND NOT starts_with(
                    substring(raw_reference_path FROM length(base_scheme) + 2),
                    '//'
                ) THEN substring(raw_reference_path FROM length(base_scheme) + 2)
                ELSE raw_reference_path
            END AS reference_path
        FROM reference_parts
    ),
    target AS (
        SELECT
            *,
            CASE
                WHEN reference = ''
                    OR regexp_matches(reference_path, '^[A-Za-z][A-Za-z0-9+.-]*:')
                    OR starts_with(reference_path, '//') THEN NULL
                WHEN starts_with(reference_path, '/') THEN reference_path
                WHEN reference_path = '' THEN base_path
                ELSE regexp_replace(base_path, '[^/]*$', '') || reference_path
            END AS path_to_normalize
        FROM resolved_reference
    ),
    segments AS (
        SELECT target.*, segment, ordinal::BIGINT AS ordinal
        FROM target,
        unnest(string_split(coalesce(path_to_normalize, ''), '/'))
            WITH ORDINALITY value(segment, ordinal)
    ),
    walk(ordinal, stack) AS (
        SELECT 0::BIGINT, []::VARCHAR[]
        UNION ALL
        SELECT
            walk.ordinal + 1,
            CASE
                WHEN segments.segment IN ('', '.') THEN walk.stack
                WHEN segments.segment = '..' THEN CASE
                    WHEN len(walk.stack) = 0 THEN walk.stack
                    ELSE list_slice(walk.stack, 1, len(walk.stack) - 1)
                END
                ELSE list_append(walk.stack, segments.segment)
            END
        FROM walk
        JOIN segments ON segments.ordinal = walk.ordinal + 1
    ),
    normalized AS (
        SELECT
            target.*,
            CASE
                WHEN path_to_normalize IS NULL THEN NULL
                ELSE '/' || array_to_string(walk.stack, '/')
                    || CASE
                        WHEN path_to_normalize <> '/'
                            AND (
                                ends_with(path_to_normalize, '/')
                                OR regexp_matches(path_to_normalize, '(^|/)(\.|\.\.)$')
                            ) AND len(walk.stack) > 0 THEN '/'
                        ELSE ''
                    END
            END AS normalized_path
        FROM target
        LEFT JOIN walk ON walk.ordinal = (SELECT max(ordinal) FROM segments)
    )
    SELECT CASE
        WHEN source IS NULL OR href IS NULL THEN NULL
        WHEN reference = '' THEN base
        WHEN regexp_matches(reference_path, '^[A-Za-z][A-Za-z0-9+.-]*:') THEN reference
        WHEN starts_with(reference_path, '//')
            THEN base_scheme || ':' || reference_path || reference_query || fragment
        WHEN reference_path = ''
            THEN base_scheme || '://' || base_authority || base_path
                || CASE WHEN reference_query <> '' THEN reference_query ELSE base_query END
                || fragment
        ELSE base_scheme || '://' || base_authority || normalized_path
            || reference_query || fragment
    END
    FROM normalized
);
