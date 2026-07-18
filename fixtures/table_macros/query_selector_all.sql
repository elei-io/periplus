CREATE MACRO macros.query_selector_all(
    selector,
    document_id := ''
) AS TABLE (
    WITH RECURSIVE
    parameters AS MATERIALIZED (
        SELECT
            trim(selector) AS selector_text,
            nullif(document_id, '') AS scoped_document_id
    ),
    scan (
        position,
        character,
        bracket_depth,
        parenthesis_depth,
        quote_character,
        escaped
    ) AS (
        SELECT
            1,
            substring(selector_text, 1, 1),
            0,
            0,
            '',
            false
        FROM parameters
        WHERE selector_text <> ''

        UNION ALL

        SELECT
            position + 1,
            substring(selector_text, position + 1, 1),
            CASE
                WHEN quote_character = '' AND character = '['
                    THEN bracket_depth + 1
                WHEN quote_character = '' AND character = ']'
                    THEN bracket_depth - 1
                ELSE bracket_depth
            END,
            CASE
                WHEN quote_character = '' AND character = '('
                    THEN parenthesis_depth + 1
                WHEN quote_character = '' AND character = ')'
                    THEN parenthesis_depth - 1
                ELSE parenthesis_depth
            END,
            CASE
                WHEN escaped THEN quote_character
                WHEN quote_character <> '' AND character = quote_character THEN ''
                WHEN quote_character = '' AND character IN ('''', '"') THEN character
                ELSE quote_character
            END,
            CASE
                WHEN escaped THEN false
                WHEN quote_character <> '' AND character = '\' THEN true
                ELSE false
            END
        FROM scan
        CROSS JOIN parameters
        WHERE position < length(selector_text)
    ),
    classified_characters AS (
        SELECT
            *,
            CASE
                WHEN bracket_depth = 0
                    AND parenthesis_depth = 0
                    AND quote_character = ''
                    AND character = ',' THEN 'comma'
                WHEN bracket_depth = 0
                    AND parenthesis_depth = 0
                    AND quote_character = ''
                    AND character = '>' THEN 'child'
                WHEN bracket_depth = 0
                    AND parenthesis_depth = 0
                    AND quote_character = ''
                    AND character = '+' THEN 'adjacent'
                WHEN bracket_depth = 0
                    AND parenthesis_depth = 0
                    AND quote_character = ''
                    AND character = '~' THEN 'sibling'
                WHEN bracket_depth = 0
                    AND parenthesis_depth = 0
                    AND quote_character = ''
                    AND regexp_matches(character, '^[ \t\r\n\f]$') THEN 'descendant'
                ELSE 'compound'
            END AS token_kind
        FROM scan
    ),
    marked_characters AS (
        SELECT
            *,
            sum(
                CASE
                    WHEN token_kind <> lag_kind OR token_kind <> 'compound' THEN 1
                    ELSE 0
                END
            ) OVER (ORDER BY position) AS run_id
        FROM (
            SELECT
                *,
                coalesce(
                    lag(token_kind) OVER (ORDER BY position),
                    ''
                ) AS lag_kind
            FROM classified_characters
        )
    ),
    raw_tokens AS (
        SELECT
            run_id,
            min(position) AS token_position,
            token_kind,
            string_agg(character, '' ORDER BY position) AS token_text
        FROM marked_characters
        GROUP BY run_id, token_kind
    ),
    neighboring_tokens AS (
        SELECT
            *,
            lag(token_kind) OVER (ORDER BY token_position) AS previous_kind,
            lead(token_kind) OVER (ORDER BY token_position) AS next_kind
        FROM raw_tokens
    ),
    grouped_tokens AS (
        SELECT
            *,
            sum(CASE WHEN token_kind = 'comma' THEN 1 ELSE 0 END)
                OVER (ORDER BY token_position) AS selector_group
        FROM neighboring_tokens
    ),
    tokens AS (
        SELECT *
        FROM grouped_tokens
        WHERE token_kind <> 'comma'
          AND NOT (
              token_kind = 'descendant'
              AND (
                  previous_kind IN ('comma', 'child', 'adjacent', 'sibling')
                  OR next_kind IN ('comma', 'child', 'adjacent', 'sibling')
                  OR previous_kind IS NULL
                  OR next_kind IS NULL
              )
          )
    ),
    ordered_tokens AS (
        SELECT
            *,
            lag(token_kind) OVER (
                PARTITION BY selector_group ORDER BY token_position
            ) AS combinator
        FROM tokens
    ),
    selector_steps AS (
        SELECT
            selector_group,
            row_number() OVER (
                PARTITION BY selector_group ORDER BY token_position
            ) AS step_number,
            token_text AS compound,
            combinator
        FROM ordered_tokens
        WHERE token_kind = 'compound'
    ),
    parsed_steps AS (
        SELECT
            *,
            regexp_replace(compound, '\[[^][]+\]', '', 'g')
                AS compound_without_attributes,
            regexp_extract_all(compound, '\[[^][]+\]')
                AS attribute_tokens
        FROM selector_steps
    ),
    validated_steps AS MATERIALIZED (
        SELECT
            *,
            CASE
                WHEN trim(
                    regexp_replace(
                        regexp_replace(
                            regexp_replace(
                                regexp_replace(
                                    compound_without_attributes,
                                    '^(\*|[A-Za-z][A-Za-z0-9_-]*)',
                                    ''
                                ),
                                '[.#][A-Za-z_][A-Za-z0-9_-]*',
                                '',
                                'g'
                            ),
                            ':(first-child|last-child|only-child|empty)',
                            '',
                            'g'
                        ),
                        ':nth-child\([0-9]+\)',
                        '',
                        'g'
                    )
                ) <> ''
                    THEN error(
                        'unsupported query selector compound: ' || compound
                    )
                ELSE lower(
                    regexp_extract(
                        compound_without_attributes,
                        '^([A-Za-z][A-Za-z0-9_-]*|\*)',
                        1
                    )
                )
            END AS required_tag,
            list_transform(
                regexp_extract_all(
                    compound_without_attributes,
                    '#[A-Za-z_][A-Za-z0-9_-]*'
                ),
                item -> substring(item, 2)
            ) AS required_ids,
            list_transform(
                regexp_extract_all(
                    compound_without_attributes,
                    '\.[A-Za-z_][A-Za-z0-9_-]*'
                ),
                item -> substring(item, 2)
            ) AS required_classes,
            regexp_matches(compound_without_attributes, ':first-child')
                AS requires_first_child,
            regexp_matches(compound_without_attributes, ':last-child')
                AS requires_last_child,
            regexp_matches(compound_without_attributes, ':only-child')
                AS requires_only_child,
            regexp_matches(compound_without_attributes, ':empty')
                AS requires_empty,
            try_cast(
                regexp_extract(
                    compound_without_attributes,
                    ':nth-child\(([0-9]+)\)',
                    1
                ) AS BIGINT
            ) AS required_child_index
        FROM parsed_steps
    ),
    attribute_requirements AS MATERIALIZED (
        SELECT
            step.selector_group,
            step.step_number,
            lower(
                regexp_extract(
                    substring(token, 2, length(token) - 2),
                    '^([A-Za-z_:][A-Za-z0-9_:-]*)',
                    1
                )
            ) AS attribute_name,
            regexp_extract(
                substring(token, 2, length(token) - 2),
                '(\^=|\$=|\*=|~=|\|=|=)',
                1
            ) AS attribute_operator,
            CASE
                WHEN left(raw_value, 1) IN ('''', '"')
                    AND right(raw_value, 1) = left(raw_value, 1)
                    THEN substring(raw_value, 2, length(raw_value) - 2)
                ELSE raw_value
            END AS expected_value
        FROM validated_steps AS step
        CROSS JOIN unnest(step.attribute_tokens) AS item(token)
        CROSS JOIN LATERAL (
            SELECT trim(
                regexp_replace(
                    substring(token, 2, length(token) - 2),
                    '^[A-Za-z_:][A-Za-z0-9_:-]*\s*(\^=|\$=|\*=|~=|\|=|=)?\s*',
                    ''
                )
            ) AS raw_value
        )
    ),
    document_elements AS MATERIALIZED (
        SELECT element.*
        FROM elements AS element
        CROSS JOIN parameters
        WHERE parameters.scoped_document_id IS NULL
           OR element.document_id = parameters.scoped_document_id
    ),
    selector_features AS MATERIALIZED (
        SELECT coalesce(
            bool_or(
                requires_first_child
                OR requires_last_child
                OR requires_only_child
                OR required_child_index IS NOT NULL
                OR combinator = 'adjacent'
            ),
            false
        ) AS requires_sibling_positions
        FROM validated_steps
    ),
    positioned_elements AS MATERIALIZED (
        SELECT
            element.document_id,
            element.element_index,
            row_number() OVER (
                PARTITION BY element.document_id, element.parent_index
                ORDER BY element.element_index
            ) AS child_index,
            count(*) OVER (
                PARTITION BY element.document_id, element.parent_index
            ) AS sibling_count,
            lag(element.element_index) OVER (
                PARTITION BY element.document_id, element.parent_index
                ORDER BY element.element_index
            ) AS previous_sibling_index
        FROM document_elements AS element
        CROSS JOIN selector_features
        WHERE selector_features.requires_sibling_positions
    ),
    base_compound_matches AS MATERIALIZED (
        SELECT
            element.document_id,
            element.element_index,
            element.parent_index,
            element.subtree_end_index,
            step.selector_group,
            step.step_number,
            step.combinator,
            step.requires_first_child,
            step.requires_last_child,
            step.requires_only_child,
            step.required_child_index,
            coalesce(
                step.requires_first_child
                OR step.requires_last_child
                OR step.requires_only_child
                OR step.required_child_index IS NOT NULL
                OR step.combinator = 'adjacent',
                false
            ) AS requires_sibling_positions
        FROM document_elements AS element
        CROSS JOIN validated_steps AS step
        WHERE (step.required_tag IN ('', '*') OR lower(element.tag) = step.required_tag)
          AND NOT EXISTS (
              SELECT 1
              FROM unnest(step.required_ids) AS required(item)
              WHERE macros.get_attribute(element.attributes, 'id') <> item
                 OR macros.get_attribute(element.attributes, 'id') IS NULL
          )
          AND NOT EXISTS (
              SELECT 1
              FROM unnest(step.required_classes) AS required(item)
              WHERE NOT list_contains(
                      regexp_split_to_array(
                          coalesce(
                              macros.get_attribute(element.attributes, 'class'),
                              ''
                          ),
                          '[ \t\r\n\f]+'
                      ),
                      item
                  )
          )
          AND NOT EXISTS (
              SELECT 1
              FROM attribute_requirements AS requirement
              WHERE requirement.selector_group = step.selector_group
                AND requirement.step_number = step.step_number
                AND NOT (
                    macros.has_attribute(
                        element.attributes,
                        requirement.attribute_name
                    )
                    AND CASE requirement.attribute_operator
                        WHEN '' THEN true
                        WHEN '=' THEN macros.get_attribute(
                            element.attributes,
                            requirement.attribute_name
                        ) = requirement.expected_value
                        WHEN '^=' THEN starts_with(
                            macros.get_attribute(
                                element.attributes,
                                requirement.attribute_name
                            ),
                            requirement.expected_value
                        )
                        WHEN '$=' THEN ends_with(
                            macros.get_attribute(
                                element.attributes,
                                requirement.attribute_name
                            ),
                            requirement.expected_value
                        )
                        WHEN '*=' THEN contains(
                            macros.get_attribute(
                                element.attributes,
                                requirement.attribute_name
                            ),
                            requirement.expected_value
                        )
                        WHEN '~=' THEN list_contains(
                            regexp_split_to_array(
                                macros.get_attribute(
                                    element.attributes,
                                    requirement.attribute_name
                                ),
                                '[ \t\r\n\f]+'
                            ),
                            requirement.expected_value
                        )
                        WHEN '|=' THEN (
                            macros.get_attribute(
                                element.attributes,
                                requirement.attribute_name
                            ) = requirement.expected_value
                            OR starts_with(
                                macros.get_attribute(
                                    element.attributes,
                                    requirement.attribute_name
                                ),
                                requirement.expected_value || '-'
                            )
                        )
                        ELSE false
                    END
                )
          )
          AND (
              NOT step.requires_empty
              OR (
                  NOT macros.has_text(element.text_direct)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM document_elements AS child
                      WHERE child.document_id = element.document_id
                        AND child.parent_index = element.element_index
                  )
              )
          )
    ),
    compound_matches AS MATERIALIZED (
        SELECT
            base.document_id,
            base.element_index,
            base.parent_index,
            base.subtree_end_index,
            NULL::BIGINT AS previous_sibling_index,
            base.selector_group,
            base.step_number,
            base.combinator
        FROM base_compound_matches AS base
        WHERE NOT base.requires_sibling_positions

        UNION ALL

        SELECT
            base.document_id,
            base.element_index,
            base.parent_index,
            base.subtree_end_index,
            position.previous_sibling_index,
            base.selector_group,
            base.step_number,
            base.combinator
        FROM base_compound_matches AS base
        JOIN positioned_elements AS position
          ON position.document_id = base.document_id
         AND position.element_index = base.element_index
        WHERE base.requires_sibling_positions
          AND (
              NOT base.requires_first_child
              OR position.child_index = 1
          )
          AND (
              NOT base.requires_last_child
              OR position.child_index = position.sibling_count
          )
          AND (
              NOT base.requires_only_child
              OR position.sibling_count = 1
          )
          AND (
              base.required_child_index IS NULL
              OR position.child_index = base.required_child_index
          )
    ),
    selector_ends AS MATERIALIZED (
        SELECT selector_group, max(step_number) AS step_number
        FROM validated_steps
        GROUP BY selector_group
    ),
    matched_path (
        selector_group,
        step_number,
        subject_document_id,
        subject_element_index,
        related_document_id,
        related_element_index,
        related_parent_index,
        related_subtree_end_index,
        related_previous_sibling_index
    ) AS (
        SELECT
            candidate.selector_group,
            candidate.step_number,
            candidate.document_id,
            candidate.element_index,
            candidate.document_id,
            candidate.element_index,
            candidate.parent_index,
            candidate.subtree_end_index,
            candidate.previous_sibling_index
        FROM compound_matches AS candidate
        JOIN selector_ends AS final_step USING (selector_group, step_number)

        UNION ALL

        SELECT
            path.selector_group,
            related.step_number,
            path.subject_document_id,
            path.subject_element_index,
            related.document_id,
            related.element_index,
            related.parent_index,
            related.subtree_end_index,
            related.previous_sibling_index
        FROM matched_path AS path
        JOIN validated_steps AS current_step
          ON current_step.selector_group = path.selector_group
         AND current_step.step_number = path.step_number
        JOIN compound_matches AS related
          ON related.selector_group = path.selector_group
         AND related.step_number = path.step_number - 1
         AND related.document_id = path.related_document_id
         AND CASE current_step.combinator
             WHEN 'child'
                 THEN path.related_parent_index = related.element_index
             WHEN 'descendant'
                 THEN path.related_element_index > related.element_index
                  AND path.related_element_index <= related.subtree_end_index
             WHEN 'adjacent'
                 THEN path.related_previous_sibling_index = related.element_index
             WHEN 'sibling'
                 THEN path.related_parent_index IS NOT DISTINCT FROM related.parent_index
                  AND related.element_index < path.related_element_index
             ELSE false
         END
    ),
    matched_elements AS MATERIALIZED (
        SELECT DISTINCT subject_document_id, subject_element_index
        FROM matched_path
        WHERE step_number = 1
    ),
    validated_input AS MATERIALIZED (
        SELECT CASE
            WHEN selector_text = '' THEN error('query selector must not be empty')
            WHEN EXISTS (
                SELECT 1
                FROM scan
                WHERE bracket_depth < 0 OR parenthesis_depth < 0
            ) THEN error('query selector has unbalanced delimiters')
            WHEN EXISTS (
                SELECT 1
                FROM raw_tokens AS operator
                WHERE operator.token_kind IN (
                    'comma',
                    'child',
                    'adjacent',
                    'sibling'
                )
                  AND (
                      coalesce(
                          (
                              SELECT previous.token_kind
                              FROM raw_tokens AS previous
                              WHERE previous.token_position
                                    < operator.token_position
                                AND previous.token_kind <> 'descendant'
                              ORDER BY previous.token_position DESC
                              LIMIT 1
                          ),
                          ''
                      ) <> 'compound'
                      OR coalesce(
                          (
                              SELECT following.token_kind
                              FROM raw_tokens AS following
                              WHERE following.token_position
                                    > operator.token_position
                                AND following.token_kind <> 'descendant'
                              ORDER BY following.token_position
                              LIMIT 1
                          ),
                          ''
                      ) <> 'compound'
                  )
            ) THEN error('query selector has a misplaced combinator')
            ELSE true
        END AS valid
        FROM parameters
    )
    SELECT element.*
    FROM document_elements AS element
    JOIN matched_elements AS matched
      ON matched.subject_document_id = element.document_id
     AND matched.subject_element_index = element.element_index
    CROSS JOIN validated_input
    WHERE validated_input.valid
);
