-- atlas:description=Reports whether an element contains an attribute with the supplied case-insensitive name.
CREATE OR REPLACE MACRO has_attribute(attrs, attribute_name) AS (
    map_contains(
        attrs,
        CASE
            WHEN starts_with(attribute_name, '{') THEN attribute_name
            ELSE lower(attribute_name)
        END
    )
);
