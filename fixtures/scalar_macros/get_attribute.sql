-- atlas:description=Returns an element attribute value by case-insensitive name, or NULL when the attribute is absent.
CREATE OR REPLACE MACRO get_attribute(attrs, attribute_name) AS (
    map_extract_value(
        attrs,
        CASE
            WHEN starts_with(attribute_name, '{') THEN attribute_name
            ELSE lower(attribute_name)
        END
    )
);
