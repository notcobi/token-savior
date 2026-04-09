"""Typst annotator using tree-sitter-language-pack.

Extracts functions (#let name(...) = ...), variables (#let name = ...),
imports (#import ...), and show rules (#show ...) from Typst source files.
"""

from __future__ import annotations

from token_savior.models import FunctionInfo, ImportInfo, LineRange, StructuralMetadata

try:
    from tree_sitter_language_pack import get_language, get_parser

    _parser = get_parser("typst")
    _language = get_language("typst")
    _AVAILABLE = True
except Exception:
    _AVAILABLE = False


def _get_children_of_type(node, type_name: str):
    return [c for c in node.children if c.type == type_name]


def _first_child_of_type(node, type_name: str):
    for c in node.children:
        if c.type == type_name:
            return c
    return None


def _extract_params(group_node) -> list[str]:
    """Extract parameter names from a call group node: (a, b, c: 10) -> ['a', 'b', 'c']"""
    params = []
    for child in group_node.children:
        if child.type == "ident":
            params.append(child.text.decode())
        elif child.type == "tagged":
            # named param: c: 10 — ident is the first child
            ident = _first_child_of_type(child, "ident")
            if ident:
                params.append(ident.text.decode())
        elif child.type == "spread":
            # ..args
            ident = _first_child_of_type(child, "ident")
            if ident:
                params.append(".." + ident.text.decode())
    return params


def annotate_typst(text: str, source_name: str = "<source>") -> StructuralMetadata:
    """Annotate a Typst source file, extracting let-bindings, imports, and show rules."""
    lines = text.splitlines()
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line) + 1

    functions: list[FunctionInfo] = []
    imports: list[ImportInfo] = []

    if not _AVAILABLE:
        return StructuralMetadata(
            source_name=source_name,
            total_lines=len(lines),
            total_chars=len(text),
            lines=lines,
            line_char_offsets=offsets,
            functions=functions,
            imports=imports,
        )

    tree = _parser.parse(text.encode())

    def walk(node):
        # Each top-level code block is: code > # > (let | import | show | ...)
        if node.type == "let":
            _handle_let(node)
        elif node.type == "import":
            _handle_import(node)
        elif node.type == "show":
            _handle_show(node)
        else:
            for child in node.children:
                walk(child)

    def _handle_let(let_node):
        start_line = let_node.start_point[0] + 1
        end_line = let_node.end_point[0] + 1

        # Function: #let name(...) = body  →  let > call > ident
        call = _first_child_of_type(let_node, "call")
        if call:
            ident = _first_child_of_type(call, "ident")
            if not ident:
                return
            name = ident.text.decode()
            group = _first_child_of_type(call, "group")
            params = _extract_params(group) if group else []
            functions.append(FunctionInfo(
                name=name,
                qualified_name=name,
                line_range=LineRange(start_line, end_line),
                parameters=params,
                decorators=[],
                docstring=None,
                is_method=False,
                parent_class=None,
            ))
        else:
            # Variable: #let name = value  →  let > ident
            ident = _first_child_of_type(let_node, "ident")
            if not ident:
                return
            name = ident.text.decode()
            functions.append(FunctionInfo(
                name=name,
                qualified_name=name,
                line_range=LineRange(start_line, end_line),
                parameters=[],
                decorators=[],
                docstring=None,
                is_method=False,
                parent_class=None,
            ))

    def _handle_import(import_node):
        # #import "path": a, b, c  →  import > string, binding...
        string_node = _first_child_of_type(import_node, "string")
        if not string_node:
            return
        # Strip surrounding quotes from the full string text
        raw = string_node.text.decode().strip('"').strip("'")
        names = []
        for c in import_node.children:
            if c.type == "binding":
                ident = _first_child_of_type(c, "ident")
                if ident:
                    names.append(ident.text.decode())
        imports.append(ImportInfo(
            module=raw,
            names=names,
            alias=None,
            line_number=import_node.start_point[0] + 1,
            is_from_import=bool(names),
        ))

    def _handle_show(show_node):
        # Treat show rules as zero-param functions named "show:<selector>"
        start_line = show_node.start_point[0] + 1
        end_line = show_node.end_point[0] + 1
        # Find selector (ident or field before the colon)
        selector = None
        for child in show_node.children:
            if child.type == "ident":
                selector = child.text.decode()
                break
            elif child.type == "field":
                selector = child.text.decode()
                break
        name = f"show:{selector}" if selector else "show"
        functions.append(FunctionInfo(
            name=name,
            qualified_name=name,
            line_range=LineRange(start_line, end_line),
            parameters=[],
            decorators=[],
            docstring=None,
            is_method=False,
            parent_class=None,
        ))

    walk(tree.root_node)

    return StructuralMetadata(
        source_name=source_name,
        total_lines=len(lines),
        total_chars=len(text),
        lines=lines,
        line_char_offsets=offsets,
        functions=functions,
        imports=imports,
    )
