CREATE OR REPLACE MACRO web.attribute(element_attributes, attribute_name) AS
    map_extract_value(element_attributes, attribute_name);
