"""Equivalent native SQL acceptance cases; no API-side optimization layer."""

from client import literal, target

DOCUMENT = "89ee04017fb94c65660b85397c11135e9179c6f12cef0c774c01bb020a170151"


def document_cases(table: str) -> dict[str, str]:
    t = target(table)
    return {
        "document_identity": f"SELECT document_id,length(document_text) AS bytes,length(elements) AS nodes FROM {t} WHERE document_id={literal(DOCUMENT)}",
        "document_word_rare": f"SELECT document_id FROM {t} WHERE hasAllTokens(lower(document_text),['ic3','scammers']) ORDER BY document_id",
        "document_word_common": f"SELECT count() AS n FROM {t} WHERE hasToken(lower(document_text),'the')",
        "class_element": f"SELECT document_id,e.node_index FROM {t} ARRAY JOIN elements AS e WHERE has(class_tokens,'hp-icon') AND has(splitByWhitespace(e.attributes['class']),'hp-icon') ORDER BY document_id,e.node_index",
        "class_same_element": f"SELECT document_id,e.node_index FROM {t} ARRAY JOIN elements AS e WHERE hasAll(class_tokens,['usa-header','usa-header--basic']) AND hasAll(splitByWhitespace(e.attributes['class']),['usa-header','usa-header--basic']) ORDER BY document_id,e.node_index",
        "selected_headings": f"SELECT document_id,e.node_index,substringUTF8(document_text,e.text_start+1,e.text_end-e.text_start) AS text FROM {t} ARRAY JOIN elements AS e WHERE document_id={literal(DOCUMENT)} AND e.tag IN ('h1','h2','h3') ORDER BY e.node_index",
        "element_count": f"SELECT sum(length(elements)) AS n FROM {t}",
        "full_preview": f"SELECT document_id,e.node_index AS node_index,e.parent_index AS parent_index,e.subtree_end_index AS subtree_end_index,e.sibling_index AS sibling_index,e.depth AS depth,e.tag AS tag,e.namespace AS namespace,e.attributes AS attributes,e.text_direct AS text_direct,substringUTF8(document_text,e.text_start+1,e.text_end-e.text_start) AS text FROM {t} ARRAY JOIN elements AS e LIMIT 10",
        "class_cross_element_negative": f"SELECT document_id,e.node_index FROM {t} ARRAY JOIN elements AS e WHERE document_id={literal(DOCUMENT)} AND hasAll(class_tokens,['usa-header','hp-icon']) AND hasAll(splitByWhitespace(e.attributes['class']),['usa-header','hp-icon']) ORDER BY e.node_index",
    }


def element_cases(table: str, full: bool) -> dict[str, str]:
    t = target(table)
    queries = {
        "class_element": f"SELECT document_id,node_index FROM {t} WHERE has(class_tokens,'hp-icon') ORDER BY document_id,node_index",
        "class_same_element": f"SELECT document_id,node_index FROM {t} WHERE hasAll(class_tokens,['usa-header','usa-header--basic']) ORDER BY document_id,node_index",
        "direct_word_rare": f"SELECT document_id,node_index FROM {t} WHERE hasAllTokens(lower(text_direct),['ic3','scammers']) ORDER BY document_id,node_index",
        "direct_word_common": f"SELECT count() AS n FROM {t} WHERE hasToken(lower(text_direct),'the')",
        "element_count": f"SELECT count() AS n FROM {t}",
        "class_cross_element_negative": f"SELECT document_id,node_index FROM {t} WHERE document_id={literal(DOCUMENT)} AND hasAll(class_tokens,['usa-header','hp-icon']) ORDER BY node_index",
        "tag_lookup": f"SELECT document_id,node_index FROM {t} WHERE tag='h1' ORDER BY document_id,node_index LIMIT 10",
        "attribute_id": f"SELECT document_id,node_index FROM {t} WHERE attributes['id']='main-content' ORDER BY document_id,node_index LIMIT 100",
        "document_elements": f"SELECT document_id,node_index,tag,attributes,text_direct FROM {t} WHERE document_id={literal(DOCUMENT)} ORDER BY node_index",
    }
    if table in {"elements_lean", "elements_full_lean"}:
        queries["class_element_expression"] = queries["class_element"].replace(
            "class_tokens", "splitByWhitespace(attributes['class'])"
        )
        queries["class_same_element_expression"] = queries[
            "class_same_element"
        ].replace("class_tokens", "splitByWhitespace(attributes['class'])")
        queries["attribute_id_scalar"] = queries["attribute_id"].replace(
            "attributes['id']", "element_id"
        )
    if table != "elements_plain":
        queries["attribute_id_projection"] = (
            queries["attribute_id"] + " SETTINGS optimize_functions_to_subcolumns=0"
        )
    if full:
        queries["selected_headings"] = (
            f"SELECT document_id,node_index,text FROM {t} WHERE document_id={literal(DOCUMENT)} AND tag IN ('h1','h2','h3') ORDER BY node_index"
        )
        queries["subtree_word_rare"] = (
            f"SELECT document_id,node_index FROM {t} WHERE hasAllTokens(lower(text),['ic3','scammers']) ORDER BY document_id,node_index"
        )
        queries["full_preview"] = (
            f"SELECT document_id,node_index,parent_index,subtree_end_index,sibling_index,depth,tag,namespace,attributes,text_direct,text FROM {t} LIMIT 10"
        )
    else:
        queries["selected_headings"] = (
            f"SELECT e.document_id,e.node_index,substringUTF8(d.document_text,e.text_start+1,e.text_end-e.text_start) AS text FROM {t} e INNER JOIN {target('docs_plain')} d USING(document_id) WHERE e.document_id={literal(DOCUMENT)} AND e.tag IN ('h1','h2','h3') ORDER BY e.node_index"
        )
    return queries


def capture_cases(table: str, url: str) -> dict[str, str]:
    t = target(table)
    selected = f"SELECT capture_id FROM {t} WHERE url={literal(url)} ORDER BY captured_at DESC,capture_id DESC LIMIT 1"
    return {
        "url_latest": f"SELECT capture_id,url,document_id,captured_at FROM {t} WHERE url={literal(url)} ORDER BY captured_at DESC,capture_id DESC LIMIT 1",
        "selected_links": f"WITH selected AS ({selected}) SELECT l.target_url,count() AS occurrences FROM {t} c ARRAY JOIN c.links AS l WHERE c.capture_id IN (SELECT capture_id FROM selected) GROUP BY l.target_url ORDER BY occurrences DESC,l.target_url LIMIT 50",
        "host_inventory": f"SELECT host,count() AS n FROM {t} GROUP BY host ORDER BY n DESC,host LIMIT 10",
    }


def json_cases(table: str, name: str) -> dict[str, str]:
    t = target(table)
    return {
        "json_product": f"SELECT document_id,node_index,raw FROM {t} WHERE has(types,'Product') AND name={literal(name)} ORDER BY document_id,node_index",
        "json_common_type": f"SELECT count() AS n FROM {t} WHERE has(types,'Organization')",
    }
