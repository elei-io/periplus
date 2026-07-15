"""Compile structural CSS selectors into parameterized SQL over Atlas DOM rows.

This module deliberately has no API or catalogue execution integration.  It translates a
selector into an ordinary read query over the stable ``elements`` contract so callers can inspect,
save, lint, or further compose the SQL before executing it.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from cssselect2 import parser
from cssselect2.parser import SelectorError
from tinycss2.nth import parse_nth


_HTML_NAMESPACE = "http://www.w3.org/1999/xhtml"
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
_XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
_CSS_WHITESPACE_PATTERN = "[ \\t\\r\\n\\f]+"
_NTH_NAMES = {
    "nth-child",
    "nth-last-child",
    "nth-of-type",
    "nth-last-of-type",
}
_POSITION_COLUMNS = (
    "css_child_index",
    "css_child_reverse_index",
    "css_type_index",
    "css_type_reverse_index",
    "css_previous_sibling_index",
    "css_next_sibling_index",
)
_PARAMETER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class SelectorCompileError(ValueError):
    """Raised when a selector cannot be represented by the static Atlas DOM."""


class SelectorScopeRequired(SelectorCompileError):
    """Raised when a selector traverses DOM rows and therefore needs a document bound."""


@dataclass(frozen=True, slots=True)
class CompiledSelector:
    """A standalone parameterized query returning matching ``elements`` rows."""

    sql: str
    parameters: dict[str, object]
    required_parameters: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompiledSelectorPredicate:
    """A parameterized predicate that depends only on columns of one element row."""

    sql: str
    parameters: dict[str, object]


def compile_selector(
    selector: str,
    *,
    namespaces: dict[str | None, str] | None = None,
    parameter_prefix: str = "css",
    document_parameter: str | None = "document_id",
) -> CompiledSelector:
    """Compile one CSS selector list into SQL over the unqualified ``elements`` table.

    The document binding defaults to ``document_id``.  Selectors using ``:scope`` additionally
    expose a scope index binding, which defaults to the document root at index zero.  The optional
    namespacing arguments let an AST composer safely combine multiple compiled selectors.
    """

    if not isinstance(selector, str) or not selector.strip():
        raise SelectorCompileError("CSS selector must not be empty")
    if not _PARAMETER_NAME.fullmatch(parameter_prefix):
        raise SelectorCompileError("parameter prefix must be a SQL parameter name")
    if document_parameter is not None and not _PARAMETER_NAME.fullmatch(document_parameter):
        raise SelectorCompileError("document parameter must be a SQL parameter name")

    namespace_map = dict(namespaces or {})
    parsed = _parse_selector(selector, namespace_map)

    uses_positions = any(_uses_window_columns(item.parsed_tree) for item in parsed)
    compiler = _Compiler(
        namespace_map,
        table="css_elements" if uses_positions else "css_document",
        parameter_prefix=parameter_prefix,
    )
    subject = compiler.alias()
    predicates = [compiler.node(item.parsed_tree, subject) for item in parsed]
    where = _or(predicates)
    selected = (
        f"{subject}.* EXCLUDE ({', '.join(_POSITION_COLUMNS)})"
        if uses_positions
        else f"{subject}.*"
    )
    document_filter = (
        f" WHERE document_id = ${document_parameter}"
        if document_parameter is not None
        else ""
    )
    document_source = (
        "WITH css_document AS (\n"
        f"  SELECT * FROM elements{document_filter}\n"
        ")"
    )
    position_source = (
        ", css_elements AS (\n"
        "  SELECT *,\n"
        "    row_number() OVER (PARTITION BY document_id, parent_index "
        "ORDER BY element_index) AS css_child_index,\n"
        "    row_number() OVER (PARTITION BY document_id, parent_index "
        "ORDER BY element_index DESC) AS css_child_reverse_index,\n"
        "    row_number() OVER (PARTITION BY document_id, parent_index, namespace_uri, tag "
        "ORDER BY element_index) AS css_type_index,\n"
        "    row_number() OVER (PARTITION BY document_id, parent_index, namespace_uri, tag "
        "ORDER BY element_index DESC) AS css_type_reverse_index\n"
        "    , lag(element_index) OVER (PARTITION BY document_id, parent_index "
        "ORDER BY element_index) AS css_previous_sibling_index\n"
        "    , lead(element_index) OVER (PARTITION BY document_id, parent_index "
        "ORDER BY element_index) AS css_next_sibling_index\n"
        "  FROM css_document\n"
        ")"
        if uses_positions
        else ""
    )
    sql = (
        document_source
        + position_source
        + "\n"
        + f"SELECT {selected}\n"
        f"FROM {compiler.table} AS {subject}\n"
        f"WHERE {where}"
    )
    return CompiledSelector(
        sql=sql,
        parameters=compiler.parameters,
        required_parameters=(document_parameter,) if document_parameter is not None else (),
    )


def compile_selector_predicate(
    selector: str,
    *,
    subject: str,
    namespaces: dict[str | None, str] | None = None,
    parameter_prefix: str = "css",
) -> CompiledSelectorPredicate:
    """Compile a selector that can be evaluated from one ``elements`` row.

    Row-local selectors can remain directly inside the caller's ``WHERE`` clause, allowing the
    database to preserve ordinary scan and ``LIMIT`` behavior across the global elements table.
    Selectors that inspect ancestors, descendants, children, or siblings deliberately fail here
    so the AST layer can use the document-bounded relational compiler instead.
    """

    if not _PARAMETER_NAME.fullmatch(subject):
        raise SelectorCompileError("subject must be a SQL table alias")
    if not _PARAMETER_NAME.fullmatch(parameter_prefix):
        raise SelectorCompileError("parameter prefix must be a SQL parameter name")
    namespace_map = dict(namespaces or {})
    parsed = _parse_selector(selector, namespace_map)
    if any(not _is_row_local(item.parsed_tree) for item in parsed):
        raise SelectorScopeRequired(
            "CSS selector traverses DOM structure and requires a document_id or crawl_id bound"
        )
    compiler = _Compiler(
        namespace_map,
        table="elements",
        parameter_prefix=parameter_prefix,
    )
    return CompiledSelectorPredicate(
        sql=_or([compiler.node(item.parsed_tree, subject) for item in parsed]),
        parameters=compiler.parameters,
    )


class _Compiler:
    def __init__(
        self,
        namespaces: dict[str | None, str],
        *,
        table: str,
        parameter_prefix: str,
    ) -> None:
        self.namespaces = namespaces
        self.table = table
        self.parameter_prefix = parameter_prefix
        self.parameters: dict[str, object] = {}
        self._alias_index = 0
        self._parameter_index = 0

    def alias(self) -> str:
        alias = f"css_e{self._alias_index}"
        self._alias_index += 1
        return alias

    def parameter(
        self,
        stem: str,
        value: object,
        *,
        stable_name: bool = False,
    ) -> str:
        name = (
            stem
            if stable_name and self.parameter_prefix == "css"
            else f"{self.parameter_prefix}_{stem}"
            if stable_name
            else f"{self.parameter_prefix}_{stem}_{self._parameter_index}"
        )
        self._parameter_index += 1
        self.parameters[name] = value
        return f"${name}"

    def stable_parameter_name(self, stem: str) -> str:
        return stem if self.parameter_prefix == "css" else f"{self.parameter_prefix}_{stem}"

    def node(self, node: Any, subject: str) -> str:
        if isinstance(node, parser.CombinedSelector):
            related = self.alias()
            if node.combinator == " ":
                ranked = self.alias()
                left_matches = self.node(node.left, related)
                return _and(
                    [
                        self.node(node.right, subject),
                        (
                            f"({subject}.document_id, {subject}.element_index) IN ("
                            f"SELECT {ranked}.document_id, {ranked}.element_index FROM ("
                            f"SELECT {related}.document_id, {related}.element_index, "
                            f"max(CASE WHEN {left_matches} THEN "
                            f"{related}.subtree_end_index END) OVER (PARTITION BY "
                            f"{related}.document_id ORDER BY {related}.element_index "
                            f"ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) "
                            f"AS css_ancestor_end FROM {self.table} AS {related}"
                            f") AS {ranked} WHERE {ranked}.css_ancestor_end >= "
                            f"{ranked}.element_index)"
                        ),
                    ]
                )
            if node.combinator == "~":
                return _and(
                    [
                        self.node(node.right, subject),
                        (
                            f"{subject}.element_index > coalesce((SELECT min("
                            f"{related}.element_index) FROM {self.table} AS {related} "
                            f"WHERE {self.is_sibling(related, subject)} AND "
                            f"{self.node(node.left, related)}), 2147483647)"
                        ),
                    ]
                )
            return _and(
                [
                    self.node(node.right, subject),
                    self.exists(
                        related,
                        _and(
                            [
                                self.relation(related, subject, node.combinator),
                                self.node(node.left, related),
                            ]
                        ),
                    ),
                ]
            )
        if isinstance(node, parser.CompoundSelector):
            return _and([self.simple(item, subject) for item in node.simple_selectors])
        raise SelectorCompileError(f"unsupported selector node {type(node).__name__}")

    def relative_node(
        self,
        node: Any,
        subject: str,
        anchor: str,
        initial_combinator: str,
    ) -> str:
        """Match a complex selector whose left edge is relative to ``anchor``."""

        if isinstance(node, parser.CombinedSelector):
            related = self.alias()
            return _and(
                [
                    self.node(node.right, subject),
                    self.exists(
                        related,
                        _and(
                            [
                                self.relation(related, subject, node.combinator),
                                self.relative_node(
                                    node.left,
                                    related,
                                    anchor,
                                    initial_combinator,
                                ),
                            ]
                        ),
                    ),
                ]
            )
        return _and(
            [
                self.node(node, subject),
                self.relation(anchor, subject, initial_combinator),
            ]
        )

    def simple(self, selector: Any, subject: str) -> str:
        if isinstance(selector, parser.LocalNameSelector):
            if selector.local_name == selector.lower_local_name:
                value = self.parameter("tag", selector.local_name)
                return f"{subject}.tag = {value}"
            html_namespace = self.parameter("namespace", _HTML_NAMESPACE)
            html_value = self.parameter("tag", selector.lower_local_name)
            foreign_value = self.parameter("tag", selector.local_name)
            return (
                f"((({subject}.namespace_uri = {html_namespace}) AND "
                f"{subject}.tag = {html_value}) OR "
                f"(({subject}.namespace_uri IS NULL OR "
                f"{subject}.namespace_uri <> {html_namespace}) AND "
                f"{subject}.tag = {foreign_value}))"
            )
        if isinstance(selector, parser.NamespaceSelector):
            if selector.namespace == "":
                return f"{subject}.namespace_uri IS NULL"
            value = self.parameter("namespace", selector.namespace)
            return f"{subject}.namespace_uri = {value}"
        if isinstance(selector, parser.IDSelector):
            return self.attribute_equals(subject, "id", selector.ident, case_sensitive=True)
        if isinstance(selector, parser.ClassSelector):
            key = self.parameter("attribute", "class")
            value = self.parameter("class", selector.class_name)
            return (
                f"list_contains(regexp_split_to_array(trim(coalesce("
                f"map_extract_value({subject}.attributes, {key}), '')), "
                f"'{_CSS_WHITESPACE_PATTERN}'), {value})"
            )
        if isinstance(selector, parser.AttributeSelector):
            return self.attribute(selector, subject)
        if isinstance(selector, parser.NegationSelector):
            return f"NOT {_or([self.node(item.parsed_tree, subject) for item in selector.selector_list])}"
        if isinstance(
            selector,
            (parser.MatchesAnySelector, parser.SpecificityAdjustmentSelector),
        ):
            return _or([self.node(item.parsed_tree, subject) for item in selector.selector_list])
        if isinstance(selector, parser.RelationalSelector):
            alternatives: list[str] = []
            for relative in selector.selector_list:
                candidate = self.alias()
                if (
                    relative.combinator == " "
                    and isinstance(relative.selector.parsed_tree, parser.CompoundSelector)
                ):
                    ranked = self.alias()
                    candidate_matches = self.node(
                        relative.selector.parsed_tree,
                        candidate,
                    )
                    alternatives.append(
                        f"({subject}.document_id, {subject}.element_index) IN ("
                        f"SELECT {ranked}.document_id, {ranked}.element_index FROM ("
                        f"SELECT {candidate}.document_id, {candidate}.element_index, "
                        f"{candidate}.subtree_end_index, min(CASE WHEN "
                        f"{candidate_matches} THEN {candidate}.element_index END) "
                        f"OVER (PARTITION BY {candidate}.document_id ORDER BY "
                        f"{candidate}.element_index ROWS BETWEEN 1 FOLLOWING AND "
                        f"UNBOUNDED FOLLOWING) AS css_next_match FROM {self.table} "
                        f"AS {candidate}) AS {ranked} WHERE "
                        f"{ranked}.css_next_match <= {ranked}.subtree_end_index)"
                    )
                    continue
                if (
                    relative.combinator == "~"
                    and isinstance(relative.selector.parsed_tree, parser.CompoundSelector)
                ):
                    alternatives.append(
                        f"{subject}.element_index < coalesce((SELECT max("
                        f"{candidate}.element_index) FROM {self.table} AS {candidate} "
                        f"WHERE {self.is_sibling(candidate, subject)} AND "
                        f"{self.node(relative.selector.parsed_tree, candidate)}), -1)"
                    )
                    continue
                alternatives.append(
                    self.exists(
                        candidate,
                        self.relative_node(
                            relative.selector.parsed_tree,
                            candidate,
                            subject,
                            relative.combinator,
                        ),
                    )
                )
            return _or(alternatives)
        if isinstance(selector, parser.PseudoClassSelector):
            return self.pseudo_class(selector.name, subject)
        if isinstance(selector, parser.FunctionalPseudoClassSelector):
            if selector.name in _NTH_NAMES:
                return self.nth(selector, subject)
            if selector.name == "lang":
                return self.language(selector.arguments, subject)
            if selector.name == "dir":
                return self.direction(selector.arguments, subject)
            raise SelectorCompileError(
                f":{selector.name}() is not supported by the static Atlas DOM compiler"
            )
        raise SelectorCompileError(f"unsupported simple selector {type(selector).__name__}")

    def attribute(self, selector: parser.AttributeSelector, subject: str) -> str:
        value_text = selector.value or ""
        if selector.operator == "~=" and (
            value_text.strip() != value_text or len(value_text.split()) != 1
        ):
            return "FALSE"
        if selector.operator in {"^=", "$=", "*="} and not value_text:
            return "FALSE"

        key = self.attribute_key_expression(selector, subject)
        present = f"map_contains({subject}.attributes, {key})"
        if selector.operator is None:
            return present

        raw = f"map_extract_value({subject}.attributes, {key})"
        case_sensitive = selector.case_sensitive is not False
        value = self.parameter(
            "attribute_value",
            value_text if case_sensitive else value_text.lower(),
        )
        compared = raw if case_sensitive else f"lower({raw})"
        operator = selector.operator
        if operator == "=":
            test = f"{compared} = {value}"
        elif operator == "~=":
            test = (
                f"list_contains(regexp_split_to_array(trim(coalesce({compared}, '')), "
                f"'{_CSS_WHITESPACE_PATTERN}'), {value})"
            )
        elif operator == "|=":
            test = f"({compared} = {value} OR starts_with({compared}, {value} || '-'))"
        elif operator in {"^=", "$=", "*="}:
            function = {"^=": "starts_with", "$=": "ends_with", "*=": "contains"}[operator]
            test = f"{function}({compared}, {value})"
        else:
            raise SelectorCompileError(f"unsupported attribute operator {operator!r}")
        return _and([present, test])

    def attribute_key_expression(
        self,
        selector: parser.AttributeSelector,
        subject: str,
    ) -> str:
        if selector.namespace is None:
            raise SelectorCompileError(
                "wildcard attribute namespaces are not yet supported"
            )
        if selector.namespace:
            key = self.parameter(
                "attribute",
                f"{{{selector.namespace}}}{selector.name}",
            )
            return key
        if selector.name == selector.lower_name:
            return self.parameter("attribute", selector.name)
        html_namespace = self.parameter("namespace", _HTML_NAMESPACE)
        html_key = self.parameter("attribute", selector.lower_name)
        foreign_key = self.parameter("attribute", selector.name)
        return (
            f"CASE WHEN {subject}.namespace_uri = {html_namespace} "
            f"THEN {html_key} ELSE {foreign_key} END"
        )

    def attribute_equals(
        self,
        subject: str,
        key_value: str,
        expected: str,
        *,
        case_sensitive: bool,
    ) -> str:
        key = self.parameter("attribute", key_value)
        value = self.parameter(
            "attribute_value",
            expected if case_sensitive else expected.lower(),
        )
        expression = f"map_extract_value({subject}.attributes, {key})"
        if not case_sensitive:
            expression = f"lower({expression})"
        return _and(
            [
                f"map_contains({subject}.attributes, {key})",
                f"{expression} = {value}",
            ]
        )

    def pseudo_class(self, name: str, subject: str) -> str:
        if name == "root":
            return f"{subject}.parent_index IS NULL"
        if name == "scope":
            stable_name = self.stable_parameter_name("scope_element_index")
            scope = self.parameters.get(stable_name)
            if scope is None:
                scope_parameter = self.parameter(
                    "scope_element_index", 0, stable_name=True
                )
            else:
                scope_parameter = f"${stable_name}"
            return f"{subject}.element_index = {scope_parameter}"
        if name == "empty":
            child = self.alias()
            return _and(
                [
                    f"{subject}.text_direct = ''",
                    f"NOT {self.exists(child, self.is_direct_child(subject, child))}",
                ]
            )
        if name in {
            "first-child",
            "last-child",
            "only-child",
            "first-of-type",
            "last-of-type",
            "only-of-type",
        }:
            return self.position_keyword(name, subject)
        if name == "any-link":
            href = self.parameter("attribute", "href")
            xlink_href = self.parameter("attribute", f"{{{_XLINK_NAMESPACE}}}href")
            html_namespace = self.parameter("namespace", _HTML_NAMESPACE)
            svg_namespace = self.parameter("namespace", _SVG_NAMESPACE)
            return (
                f"((({subject}.namespace_uri = {html_namespace} AND "
                f"{subject}.tag IN ('a', 'area', 'link')) OR "
                f"({subject}.namespace_uri = {svg_namespace} AND {subject}.tag = 'a')) "
                f"AND (map_contains({subject}.attributes, {href}) OR "
                f"map_contains({subject}.attributes, {xlink_href})))"
            )
        raise SelectorCompileError(
            f":{name} requires browser state or is not supported by the static Atlas DOM compiler"
        )

    def position_keyword(self, name: str, subject: str) -> str:
        previous = self.alias()
        following = self.alias()
        same_type_previous = self.same_type(previous, subject)
        same_type_following = self.same_type(following, subject)
        before = _and(
            [self.is_sibling(previous, subject), f"{previous}.element_index < {subject}.element_index"]
        )
        after = _and(
            [self.is_sibling(following, subject), f"{following}.element_index > {subject}.element_index"]
        )
        tests = {
            "first-child": f"NOT {self.exists(previous, before)}",
            "last-child": f"NOT {self.exists(following, after)}",
            "only-child": _and(
                [f"NOT {self.exists(previous, before)}", f"NOT {self.exists(following, after)}"]
            ),
            "first-of-type": f"NOT {self.exists(previous, _and([before, same_type_previous]))}",
            "last-of-type": f"NOT {self.exists(following, _and([after, same_type_following]))}",
            "only-of-type": _and(
                [
                    f"NOT {self.exists(previous, _and([before, same_type_previous]))}",
                    f"NOT {self.exists(following, _and([after, same_type_following]))}",
                ]
            ),
        }
        return tests[name]

    def nth(self, selector: parser.FunctionalPseudoClassSelector, subject: str) -> str:
        nth_tokens, selector_tokens = _split_nth_arguments(selector.arguments)
        parsed_nth = parse_nth(nth_tokens)
        if parsed_nth is None:
            raise SelectorCompileError(f"invalid :{selector.name}() expression")
        if selector_tokens and selector.name not in {"nth-child", "nth-last-child"}:
            raise SelectorCompileError(f":{selector.name}() does not accept an 'of' selector")
        if selector_tokens:
            try:
                of_selectors = list(parser.parse(selector_tokens, namespaces=self.namespaces))
            except (SelectorError, TypeError, ValueError) as exc:
                raise SelectorCompileError(
                    f"invalid selector after 'of' in :{selector.name}(): {exc}"
                ) from exc
            filtered = self.alias()
            ranked = self.alias()
            filter_predicate = _or(
                [self.node(item.parsed_tree, filtered) for item in of_selectors]
            )
            order = "DESC" if selector.name == "nth-last-child" else "ASC"
            position = f"{ranked}.css_filtered_index"
            return (
                f"({subject}.document_id, {subject}.element_index) IN ("
                f"SELECT {ranked}.document_id, {ranked}.element_index FROM ("
                f"SELECT {filtered}.document_id, {filtered}.element_index, "
                f"row_number() OVER (PARTITION BY {filtered}.document_id, "
                f"{filtered}.parent_index ORDER BY {filtered}.element_index {order}) "
                f"AS css_filtered_index FROM {self.table} AS {filtered} "
                f"WHERE {filter_predicate}"
                f") AS {ranked} WHERE {_nth_condition(position, *parsed_nth)})"
            )

        position_column = {
            "nth-child": "css_child_index",
            "nth-last-child": "css_child_reverse_index",
            "nth-of-type": "css_type_index",
            "nth-last-of-type": "css_type_reverse_index",
        }[selector.name]
        return _nth_condition(f"{subject}.{position_column}", *parsed_nth)

    def language(self, arguments: list[Any], subject: str) -> str:
        values = _functional_values(arguments, "lang")
        if not values:
            raise SelectorCompileError(":lang() requires at least one language range")
        ancestor = self.alias()
        lang = (
            f"coalesce((SELECT lower(coalesce("
            f"map_extract_value({ancestor}.attributes, 'lang'), "
            f"map_extract_value({ancestor}.attributes, '{{{_XML_NAMESPACE}}}lang'))) "
            f"FROM elements AS {ancestor} WHERE "
            f"{self.is_ancestor_or_self(ancestor, subject)} AND "
            f"(map_contains({ancestor}.attributes, 'lang') OR "
            f"map_contains({ancestor}.attributes, '{{{_XML_NAMESPACE}}}lang')) "
            f"ORDER BY {ancestor}.depth DESC LIMIT 1), '')"
        )
        tests: list[str] = []
        for raw in values:
            value = self.parameter("language", raw.lower())
            tests.append(f"({lang} = {value} OR starts_with({lang}, {value} || '-'))")
        return _or(tests)

    def direction(self, arguments: list[Any], subject: str) -> str:
        values = _functional_values(arguments, "dir")
        if len(values) != 1 or values[0].lower() not in {"ltr", "rtl"}:
            raise SelectorCompileError(":dir() requires exactly one of 'ltr' or 'rtl'")
        ancestor = self.alias()
        inherited = (
            f"coalesce((SELECT lower(map_extract_value({ancestor}.attributes, 'dir')) "
            f"FROM elements AS {ancestor} WHERE "
            f"{self.is_ancestor_or_self(ancestor, subject)} AND "
            f"map_contains({ancestor}.attributes, 'dir') AND "
            f"lower(map_extract_value({ancestor}.attributes, 'dir')) IN ('ltr', 'rtl') "
            f"ORDER BY {ancestor}.depth DESC LIMIT 1), 'ltr')"
        )
        value = self.parameter("direction", values[0].lower())
        return f"{inherited} = {value}"

    def exists(self, alias: str, predicate: str) -> str:
        return f"EXISTS (SELECT 1 FROM {self.table} AS {alias} WHERE {predicate})"

    def relation(self, left: str, right: str, combinator: str) -> str:
        same_document = f"{left}.document_id = {right}.document_id"
        if combinator == " ":
            return _and(
                [
                    same_document,
                    f"{left}.element_index < {right}.element_index",
                    f"{right}.element_index <= {left}.subtree_end_index",
                ]
            )
        if combinator == ">":
            return _and(
                [same_document, f"{right}.parent_index = {left}.element_index"]
            )
        if combinator == "~":
            return _and(
                [
                    self.is_sibling(left, right),
                    f"{left}.element_index < {right}.element_index",
                ]
            )
        if combinator == "+":
            return _and(
                [
                    self.is_sibling(left, right),
                    f"{left}.element_index = {right}.css_previous_sibling_index",
                ]
            )
        raise SelectorCompileError(f"unsupported combinator {combinator!r}")

    @staticmethod
    def is_sibling(left: str, right: str) -> str:
        return _and(
            [
                f"{left}.document_id = {right}.document_id",
                f"{left}.parent_index = {right}.parent_index",
            ]
        )

    @staticmethod
    def is_direct_child(parent: str, child: str) -> str:
        return _and(
            [
                f"{parent}.document_id = {child}.document_id",
                f"{child}.parent_index = {parent}.element_index",
            ]
        )

    @staticmethod
    def is_ancestor_or_self(ancestor: str, subject: str) -> str:
        return _and(
            [
                f"{ancestor}.document_id = {subject}.document_id",
                f"{subject}.element_index BETWEEN {ancestor}.element_index "
                f"AND {ancestor}.subtree_end_index",
            ]
        )

    @staticmethod
    def same_type(left: str, right: str) -> str:
        return _and(
            [
                f"{left}.tag = {right}.tag",
                f"{left}.namespace_uri IS NOT DISTINCT FROM {right}.namespace_uri",
            ]
        )


def _split_nth_arguments(arguments: list[Any]) -> tuple[list[Any], list[Any]]:
    nth: list[Any] = []
    selector: list[Any] = []
    target = nth
    for token in arguments:
        if target is nth and token.type == "ident" and token.lower_value == "of":
            target = selector
            continue
        target.append(token)
    if target is selector and not selector:
        raise SelectorCompileError("'of' in :nth-child() requires a selector")
    return nth, selector


def _parse_selector(
    selector: str,
    namespaces: dict[str | None, str],
) -> list[Any]:
    if not isinstance(selector, str) or not selector.strip():
        raise SelectorCompileError("CSS selector must not be empty")
    try:
        parsed = list(parser.parse(selector, namespaces=namespaces))
    except (SelectorError, TypeError, ValueError) as exc:
        raise SelectorCompileError(f"invalid CSS selector: {exc}") from exc
    if not parsed:
        raise SelectorCompileError("CSS selector must not be empty")
    if any(item.pseudo_element is not None for item in parsed):
        pseudo = next(item.pseudo_element for item in parsed if item.pseudo_element is not None)
        raise SelectorCompileError(
            f"::{pseudo} selects a rendered pseudo-element, not an Atlas DOM element"
        )
    return parsed


def _is_row_local(node: Any) -> bool:
    if isinstance(node, parser.CombinedSelector):
        return False
    if isinstance(node, parser.CompoundSelector):
        return all(_is_row_local(item) for item in node.simple_selectors)
    if isinstance(
        node,
        (
            parser.LocalNameSelector,
            parser.NamespaceSelector,
            parser.IDSelector,
            parser.ClassSelector,
            parser.AttributeSelector,
        ),
    ):
        return True
    if isinstance(node, parser.PseudoClassSelector):
        return node.name in {"root", "scope", "any-link"}
    if isinstance(
        node,
        (parser.NegationSelector, parser.MatchesAnySelector, parser.SpecificityAdjustmentSelector),
    ):
        return all(_is_row_local(item.parsed_tree) for item in node.selector_list)
    return False


def _uses_window_columns(node: Any) -> bool:
    if isinstance(node, parser.CombinedSelector):
        return (
            node.combinator == "+"
            or _uses_window_columns(node.left)
            or _uses_window_columns(node.right)
        )
    if isinstance(node, parser.CompoundSelector):
        return any(_uses_window_columns(item) for item in node.simple_selectors)
    if isinstance(node, parser.FunctionalPseudoClassSelector):
        return node.name in _NTH_NAMES
    if isinstance(
        node,
        (parser.NegationSelector, parser.MatchesAnySelector, parser.SpecificityAdjustmentSelector),
    ):
        return any(_uses_window_columns(item.parsed_tree) for item in node.selector_list)
    if isinstance(node, parser.RelationalSelector):
        return any(
            item.combinator == "+"
            or _uses_window_columns(item.selector.parsed_tree)
            for item in node.selector_list
        )
    return False


def _functional_values(arguments: list[Any], name: str) -> list[str]:
    tokens = [token for token in arguments if token.type not in {"whitespace", "comment"}]
    values: list[str] = []
    expect_value = True
    for token in tokens:
        if expect_value:
            if token.type not in {"ident", "string"}:
                raise SelectorCompileError(f"invalid :{name}() arguments")
            values.append(token.value)
        elif token.type != "literal" or token.value != ",":
            raise SelectorCompileError(f"invalid :{name}() arguments")
        expect_value = not expect_value
    if tokens and expect_value:
        raise SelectorCompileError(f"invalid :{name}() arguments")
    return values


def _nth_condition(position: str, a: int, b: int) -> str:
    if a == 0:
        return f"{position} = {b}"
    if a > 0:
        return f"({position} >= {b} AND ({position} - {b}) % {a} = 0)"
    return f"({position} <= {b} AND ({b} - {position}) % {-a} = 0)"


def _and(expressions: list[str]) -> str:
    if not expressions:
        return "TRUE"
    return "(" + " AND ".join(f"({expression})" for expression in expressions) + ")"


def _or(expressions: list[str]) -> str:
    if not expressions:
        return "FALSE"
    return "(" + " OR ".join(f"({expression})" for expression in expressions) + ")"
