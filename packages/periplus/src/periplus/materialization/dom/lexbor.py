"""Read-only DOM adapter for the pinned Selectolax 0.4.11 / Lexbor 3.1 ABI.

Selectolax owns parsing and lifetime. Its public mem_id exposes the Lexbor node;
read the upstream C structs to preserve template fragments, character data and
namespaces omitted by the high-level wrapper. No native tree is mutated here.
Layouts follow the bundled lexbor/dom/interfaces and html/interfaces headers.
The locked upstream wheels and the ABI/completeness fixtures must move together.
"""

from __future__ import annotations

import ctypes as C
from importlib.metadata import version

from html5lib._inputstream import HTMLBinaryInputStream
from selectolax import lexbor as native

PARSER_NAME = "lexbor"
PARSER_VERSION = "3.1.0/selectolax-0.4.11"
PARSER_CONTRACT = "lexbor-complete-dom-v1"
if version("selectolax") != "0.4.11" or C.sizeof(C.c_void_p) != 8:
    raise RuntimeError(
        "DOM adapter requires pinned Selectolax 0.4.11 on a 64-bit platform"
    )

_P = C.c_void_p
_Size = C.c_size_t


class _Node(C.Structure):
    _fields_ = [
        (name, _P)
        for name in (
            "events",
            "local_name",
            "prefix",
            "ns",
            "owner",
            "next",
            "prev",
            "parent",
            "first_child",
            "last_child",
            "user",
        )
    ] + [("type", C.c_uint)]


class _Element(C.Structure):
    _fields_ = (
        [("node", _Node)]
        + [
            (name, _P)
            for name in (
                "upper_name",
                "qualified_name",
                "is_value",
                "first_attr",
                "last_attr",
                "attr_id",
                "attr_class",
                "style",
                "list",
            )
        ]
        + [("condition", C.c_uint), ("custom_state", C.c_uint)]
    )


class _Template(C.Structure):
    _fields_ = [("element", _Element), ("content", _P)]


class _String(C.Structure):
    _fields_ = [("data", _P), ("length", _Size)]


class _Data(C.Structure):
    _fields_ = [("node", _Node), ("data", _String)]


class _Attribute(C.Structure):
    _fields_ = [("node", _Node)] + [
        (name, _P)
        for name in (
            "upper_name",
            "qualified_name",
            "value",
            "owner",
            "next",
            "prev",
        )
    ]


# Keep the GIL: Selectolax installs Python-backed native memory allocators.
_library = C.PyDLL(native.__file__)


def _string_function(name):
    function = getattr(_library, name)
    function.argtypes = [_P, C.POINTER(_Size)]
    function.restype = _P
    return function


_element_name = _string_function("lxb_dom_element_qualified_name")
_attribute_name = _string_function("lxb_dom_attr_qualified_name")
_doctype_name = _string_function("lxb_dom_document_type_name_noi")
_instruction_target = _string_function("lxb_dom_processing_instruction_target_noi")
_NAMESPACES = {
    2: "http://www.w3.org/1999/xhtml",
    3: "http://www.w3.org/1998/Math/MathML",
    4: "http://www.w3.org/2000/svg",
    5: "http://www.w3.org/1999/xlink",
    6: "http://www.w3.org/XML/1998/namespace",
    7: "http://www.w3.org/2000/xmlns/",
}
_KINDS = {
    1: "element",
    3: "text",
    4: "text",
    7: "processing_instruction",
    8: "comment",
    9: "document",
    10: "doctype",
    11: "document_fragment",
}


def _name(function, pointer: int) -> str:
    size = _Size()
    data = function(pointer, C.byref(size))
    return C.string_at(data, size.value).decode("utf-8") if data else ""


def decode_html(source: str | bytes) -> str:
    if isinstance(source, str):
        return source
    if not isinstance(source, bytes):
        raise TypeError("HTML source must be text or bytes")
    # A fixed policy, independent of optional chardet installation: BOM, then
    # HTML meta sniffing in the first 1024 bytes, otherwise windows-1252.
    # The stream consumes the BOM and replaces malformed encoded sequences.
    return HTMLBinaryInputStream(source, useChardet=False).dataStream.read()


def records(source: str | bytes):
    """Yield enter/exit records while the parser owns every referenced pointer.

    Template content is exposed as an explicit document_fragment child. This is
    a logical containment edge: native template.content has no DOM parent.
    """
    parser = native.LexborHTMLParser(decode_html(source))
    root = parser.root.parent.mem_id
    stack = [(root, False)]
    while stack:
        pointer, closing = stack.pop()
        if closing:
            yield None
            continue
        node = _Node.from_address(pointer)
        kind = _KINDS.get(node.type)
        if kind is None:
            raise ValueError(f"unsupported Lexbor node kind {node.type}")
        name = namespace = value = None
        attributes = {}
        if node.type == 1:
            name = _name(_element_name, pointer)
            namespace = _NAMESPACES[node.ns]
            attr_pointer = _Element.from_address(pointer).first_attr
            while attr_pointer:
                attr = _Attribute.from_address(attr_pointer)
                key = _name(_attribute_name, attr_pointer)
                # Lexbor uses HTML/SVG/MathML IDs internally for unqualified
                # attributes; those are not DOM attribute namespaces.
                if attr.node.ns in (5, 6, 7):
                    key = "{" + _NAMESPACES[attr.node.ns] + "}" + key.split(":")[-1]
                data = _String.from_address(attr.value) if attr.value else None
                attributes[key] = (
                    C.string_at(data.data, data.length).decode("utf-8") if data else ""
                )
                attr_pointer = attr.next
        elif node.type == 10:
            name = _name(_doctype_name, pointer)
        elif node.type in (3, 4, 7, 8):
            data = _Data.from_address(pointer).data
            value = C.string_at(data.data, data.length).decode("utf-8")
            if node.type == 7:
                name = _name(_instruction_target, pointer)
        yield kind, name, namespace, value, dict(sorted(attributes.items()))
        stack.append((pointer, True))
        if node.type == 1 and node.ns == 2 and name == "template":
            content = _Template.from_address(pointer).content
            if content:
                stack.append((content, False))
        else:
            children = []
            child = node.first_child
            while child:
                children.append((child, False))
                child = _Node.from_address(child).next
            stack.extend(reversed(children))


def _validate_abi() -> None:
    """Fail at process startup if the pinned wheel's native layout differs."""
    if (C.sizeof(_Node), C.sizeof(_Element), C.sizeof(_Data), C.sizeof(_Attribute)) != (
        96,
        176,
        112,
        144,
    ):
        raise RuntimeError("unsupported Lexbor native struct layout")
    probe = tuple(
        record
        for record in records(
            '<template data-probe="value">text<!-- exact --></template>'
            '<svg viewBox="0 0 1 1"></svg><?probe data?>'
        )
        if record is not None
    )
    if not (
        any(row[0] == "document_fragment" for row in probe)
        and any(row[0] == "text" and row[3] == "text" for row in probe)
        and any(row[0] == "comment" and row[3] == " exact " for row in probe)
        and any(
            row[1] == "template" and row[4] == {"data-probe": "value"} for row in probe
        )
        and any(
            row[1] == "svg"
            and row[2] == _NAMESPACES[4]
            and row[4] == {"viewBox": "0 0 1 1"}
            for row in probe
        )
        and any(
            row[0] == "processing_instruction" and row[1] == "probe" for row in probe
        )
    ):
        raise RuntimeError("Lexbor native DOM completeness probe failed")


_validate_abi()
