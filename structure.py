"""Analyze changed source as text without importing patched code."""
import ast
import re
from functools import lru_cache
from pathlib import Path

HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")

@lru_cache(maxsize=64)
def baseline(path):
    text = path.read_text()
    tree = ast.parse(text)
    functions = [(min([node.lineno]+[d.lineno for d in node.decorator_list]),
                  node.body[0].lineno, node.end_lineno)
                 for node in ast.walk(tree) if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))]
    return text.splitlines(), ast.dump(tree), functions

def inspect_patch(source_root, patch, path):
    """Return equivalent, local or broad, plus affected base line numbers."""
    original = (source_root/path).resolve()
    if source_root.resolve() not in original.parents or not original.is_file():
        return "broad", set()
    lines, old_ast, functions = baseline(original)
    out = []
    cursor = 0
    edits = set()
    has_hunk = False
    for line in patch.splitlines():
        if line.startswith("@@ "):
            header = HEADER.match(line)
            if not header:
                return "broad", edits
            begin = int(header[1]) - (header[2] != "0")
            if begin < cursor or begin > len(lines):
                return "broad", edits
            out.extend(lines[cursor:begin])
            cursor = begin
            has_hunk = True
        elif has_hunk and line.startswith(("-", " ")):
            if cursor >= len(lines) or lines[cursor] != line[1:]:
                return "broad", edits
            if line.startswith("-"):
                edits.add(cursor+1)
            else:
                out.append(lines[cursor])
            cursor += 1
        elif has_hunk and line.startswith("+"):
            edits.add(max(1,cursor))
            edits.add(cursor+1)
            out.append(line[1:])
    out.extend(lines[cursor:])
    if not has_hunk:
        return "broad", edits
    try:
        new = ast.parse("\n".join(out)+"\n")
        compile(new, path, "exec")
    except (SyntaxError,ValueError,RecursionError):
        return "broad", edits
    if ast.dump(new) == old_ast:
        return "equivalent", edits
    if all(any(start < pos and body <= pos <= end for start,body,end in functions) for pos in edits):
        return "local", edits
    # A change of literals inside an existing dialect class cannot add an
    # unresolved Python name at import time. Include every observed consumer
    # of that class hierarchy, including users of derived dialects.
    if path.startswith(("sqlglot/dialects/", "sqlglot/generators/", "sqlglot/parsers/")):
        def without_values(tree):
            class HideConstants(ast.NodeTransformer):
                def visit_Constant(self, node):
                    return ast.Constant(value=None)
            return ast.dump(HideConstants().visit(tree))
        if without_values(new) == without_values(ast.parse("\n".join(lines))):
            return "dialect", edits
    return "broad", edits
