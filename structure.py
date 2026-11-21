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

@lru_cache(maxsize=4)
def enum_attributes(source_root):
    """Find attributes whose explicit assignments all construct the same enum."""
    assignments = {}
    names = set()
    for folder in ("sqlglot", "tests"):
        for path in (source_root / folder / "dialects").glob("*.py"):
            if not path.is_file():
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ClassDef) and any(
                    isinstance(base, ast.Name) and base.id in {"Enum", "AutoName"}
                    for base in node.bases
                ):
                    names.add(node.name)
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Attribute) or (isinstance(target, ast.Name) and target.id.isupper()):
                            name = target.id if isinstance(target, ast.Name) else target.attr
                            assignments.setdefault(name, []).append(node.value)
    def enum_type(value):
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
            if value.value.id in names:
                return value.value.id
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            if value.func.id in names:
                return value.func.id
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
            if value.value.id == "self" and value.attr.isupper():
                return resolved.get(value.attr)
        return None
    resolved = {}
    for key in sorted(assignments, key=lambda name: not name.isupper()):
        possibilities = {enum_type(value) for value in assignments[key]}
        if len(possibilities) == 1 and None not in possibilities:
            resolved[key] = possibilities.pop()
    return resolved

def canonical_enum_comparisons(tree, attributes):
    class Normalize(ast.NodeTransformer):
        def visit_Compare(self, node):
            self.generic_visit(node)
            if len(node.ops) != 1:
                return node
            left, right = node.left, node.comparators[0]
            if (
                isinstance(left, ast.Attribute) and isinstance(left.value, ast.Name)
                and left.value.id == "self" and isinstance(right, ast.Attribute)
                and isinstance(right.value, ast.Name)
                and attributes.get(left.attr) == right.value.id
            ):
                if isinstance(node.ops[0], ast.Is):
                    node.ops[0] = ast.Eq()
                if isinstance(node.ops[0], ast.IsNot):
                    node.ops[0] = ast.NotEq()
            return node
    return ast.dump(Normalize().visit(tree))

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
    replacing = False
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
            replacing = False
        elif has_hunk and line.startswith(("-", " ")):
            if cursor >= len(lines) or lines[cursor] != line[1:]:
                return "broad", edits
            replacing = line.startswith("-")
            if replacing:
                edits.add(cursor+1)
            else:
                out.append(lines[cursor])
            cursor += 1
        elif has_hunk and line.startswith("+"):
            if not replacing:
                edits.add(max(1, cursor))
                edits.add(cursor + 1)
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
    if path.startswith("sqlglot/dialects/") and re.search(r"(?m)^[+-].*(?:==|!=| is )", patch):
        attributes = enum_attributes(source_root)
        old_semantics = canonical_enum_comparisons(ast.parse("\n".join(lines)), attributes)
        new_semantics = canonical_enum_comparisons(ast.parse("\n".join(out)), attributes)
        if old_semantics == new_semantics:
            return "equivalent", edits
    # Lambda bodies stored in dispatch tables execute on lookup, even when
    # their enclosing assignment executes at import time.
    original_tree = ast.parse("\n".join(lines))
    for node in ast.walk(original_tree):
        if isinstance(node, ast.Lambda) and edits and all(
            node.body.lineno < pos <= node.body.end_lineno for pos in edits
        ):
            return "local", edits | {node.body.lineno}
    if all(any(start < pos and body <= pos <= end for start,body,end in functions) for pos in edits):
        return "local", edits
    # A change of literals inside an existing dialect class cannot add an
    # unresolved Python name at import time. Include every observed consumer
    # of that class hierarchy, including users of derived dialects.
    if path.startswith(("sqlglot/expressions/", "sqlglot/dialects/", "sqlglot/generators/", "sqlglot/parsers/")):
        def without_values(tree):
            class HideConstants(ast.NodeTransformer):
                def visit_Constant(self, node):
                    return ast.Constant(value=None)
            return ast.dump(HideConstants().visit(tree))
        if without_values(new) == without_values(ast.parse("\n".join(lines))):
            if path.startswith("sqlglot/expressions/"):
                for cls in ast.walk(original_tree):
                    if isinstance(cls, ast.ClassDef) and edits and all(
                        cls.lineno < pos <= cls.end_lineno for pos in edits
                    ):
                        return "expression:" + cls.name, edits
                return "broad", edits
            matching_classes = [
                cls for cls in ast.walk(original_tree)
                if isinstance(cls, ast.ClassDef) and edits and all(
                    cls.lineno < pos <= cls.end_lineno for pos in edits
                )
            ]
            if matching_classes:
                cls = min(matching_classes, key=lambda cls: cls.end_lineno - cls.lineno)
                owners = sorted(
                    (owner for owner in matching_classes if owner is not cls),
                    key=lambda owner: owner.lineno,
                )
                name = ".".join([owner.name for owner in owners] + [cls.name])
                return "dialect:" + name, edits
            return "dialect", edits
    return "broad", edits
