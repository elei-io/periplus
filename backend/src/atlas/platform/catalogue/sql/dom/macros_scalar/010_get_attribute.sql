CREATE OR REPLACE MACRO dom.get_attribute(
    element_attributes,
    attribute_name
) AS
    map_extract_value(element_attributes, attribute_name);
