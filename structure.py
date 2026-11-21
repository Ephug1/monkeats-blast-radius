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

def canonical_none_comparisons(tree, source_root):
    """Normalize None comparisons for annotated attributes with identity equality."""
    modules = {
        item.asname or item.name: node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
        and node.module and node.module.startswith("sqlglot.")
        for item in node.names
    }
    for cls in (node for node in tree.body if isinstance(node, ast.ClassDef)):
        if any(isinstance(node, ast.FunctionDef) and node.name == "__getattribute__"
               for node in cls.body):
            continue
        attributes = set()
        for node in ast.walk(cls):
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Attribute):
                continue
            if not isinstance(node.target.value, ast.Name) or node.target.value.id != "self":
                continue
            if not (node.target.attr.startswith("_") and isinstance(node.value, ast.Constant)
                    and node.value.value is None):
                continue
            hint = node.annotation
            if not (isinstance(hint, ast.BinOp) and isinstance(hint.op, ast.BitOr)
                    and isinstance(hint.left, ast.Name)
                    and isinstance(hint.right, ast.Constant) and hint.right.value is None):
                continue
            name = hint.left.id
            module = modules.get(name)
            if module is None:
                continue
            origin = source_root / (module.replace(".", "/") + ".py")
            if not origin.is_file():
                continue
            definitions = ast.parse(origin.read_text()).body
            for definition in definitions:
                if not isinstance(definition, ast.ClassDef) or definition.name != name:
                    continue
                if not definition.bases and not definition.decorator_list and not definition.keywords:
                    if not any(isinstance(member, ast.FunctionDef)
                               and member.name in {"__eq__", "__ne__"}
                               for member in definition.body):
                        attributes.add(node.target.attr)
        for node in ast.walk(cls):
            if not isinstance(node, ast.Compare) or len(node.ops) != 1:
                continue
            left, right = node.left, node.comparators[0]
            if not (isinstance(left, ast.Attribute) and isinstance(left.value, ast.Name)
                    and left.value.id == "self" and left.attr in attributes
                    and isinstance(right, ast.Constant) and right.value is None):
                continue
            if isinstance(node.ops[0], ast.Is):
                node.ops[0] = ast.Eq()
            elif isinstance(node.ops[0], ast.IsNot):
                node.ops[0] = ast.NotEq()
    return ast.dump(tree)

def changed_integer_setting(original, patched, cls, edits):
    """Recognize a single integer class setting whose type stays unchanged."""
    candidates = [
        node for node in cls.body
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value
        and node.lineno in edits
    ]
    if len(candidates) != 1:
        return None
    node = candidates[0]
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    if len(targets) != 1 or not isinstance(targets[0], ast.Name):
        return None
    name = targets[0].id
    if not name.isupper():
        return None
    # Replacing both values must erase the entire change. This excludes
    # other edits in the same hunk or class.
    candidate = next(
        (item for item in ast.walk(patched)
         if isinstance(item, type(node)) and item.lineno == node.lineno),
        None,
    )
    if candidate is None or candidate.value is None:
        return None
    try:
        if not all(type(ast.literal_eval(item.value)) is int for item in (node, candidate)):
            return None
    except (ValueError, TypeError, SyntaxError):
        return None
    old_value, new_value = node.value, candidate.value
    node.value = candidate.value = ast.Constant(value=0)
    identical = ast.dump(original) == ast.dump(patched)
    node.value, candidate.value = old_value, new_value
    return name if identical else None

@lru_cache(maxsize=16)
def setting_reads(source_root, name):
    """Find explicit Python attribute reads, retaining broad fallback for reflection."""
    reads = []
    for package in ("sqlglot", "tests"):
        for path in (source_root / package).rglob("*.py"):
            source = path.read_text()
            if name not in source:
                continue
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and node.value == name:
                    return ()
                if (isinstance(node, ast.Attribute) and node.attr == name
                        and isinstance(node.ctx, ast.Load)):
                    reads.append((str(path.relative_to(source_root)), node.lineno))
    return tuple(reads)

def fresh_expression_semantics(tree):
    """A copied, freshly built table has no prior owner or shared root."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        fn, arg = node.func, node.args[0]
        if ast.unparse(fn) != "exp.alias_" or not isinstance(arg, ast.Call):
            continue
        if ast.unparse(arg.func) != "exp.table_" or len(arg.args) != 1 or arg.keywords:
            continue
        if not isinstance(arg.args[0], (ast.Name, ast.Constant)):
            continue
        for kw in node.keywords:
            if kw.arg == "copy" and isinstance(kw.value, ast.Constant) and type(kw.value.value) is bool:
                kw.value = ast.Constant(value=True)
    return ast.dump(tree)

def version_boundary(old, new, edits):
    """Return the single version where inclusive and exclusive bounds differ."""
    matches = []
    for tree in (old, new):
        candidates = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare) or node.lineno not in edits or len(node.ops) != 1:
                continue
            if ast.unparse(node.left) != "self.dialect.version":
                continue
            op = node.ops[0]
            if not isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                continue
            try:
                boundary = ast.literal_eval(node.comparators[0])
            except (ValueError, TypeError):
                continue
            if isinstance(boundary, tuple) and boundary and all(type(part) is int for part in boundary):
                candidates.append((node, boundary, type(op)))
        if len(candidates) != 1:
            return None
        matches.append(candidates[0])
    (first, boundary, before), (second, after_boundary, after) = matches
    if boundary != after_boundary or {before, after} not in ({ast.Lt, ast.LtE}, {ast.Gt, ast.GtE}):
        return None
    first_op, second_op = first.ops, second.ops
    first.ops = second.ops = [ast.Eq()]
    identical = ast.dump(old) == ast.dump(new)
    first.ops, second.ops = first_op, second_op
    return ",".join(map(str, boundary)) if identical else None

def numeric_escape_value_edits(original, patched, cls, edits):
    """Recognize value-only changes to a tokenizer's numeric escape table."""
    if cls.name != "Tokenizer":
        return None
    values = []
    for tree in (original, patched):
        nodes = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Assign) and node.lineno in edits
            and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "NUMERIC_ESCAPES"
        ]
        if len(nodes) != 1:
            return None
        node = nodes[0]
        try:
            table = ast.literal_eval(node.value)
        except (TypeError, ValueError, SyntaxError):
            return None
        if not isinstance(table, dict) or not all(
            isinstance(key, str) and len(key) == 1 and key.isalnum()
            and isinstance(value, tuple) and len(value) == 4
            and all(type(part) is int for part in value)
            for key, value in table.items()
        ):
            return None
        values.append((node, table))
    (left, before), (right, after) = values
    if before.keys() != after.keys():
        return None
    old_value, new_value = left.value, right.value
    left.value = right.value = ast.Constant(value=None)
    identical = ast.dump(original) == ast.dump(patched)
    left.value, right.value = old_value, new_value
    if identical:
        return "".join(key for key in before if before[key] != after[key])
    return None

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
    if path.startswith("sqlglot/") and re.search(r"(?m)^[+-].*(?:==|!=| is ) None", patch):
        old_semantics = canonical_none_comparisons(ast.parse("\n".join(lines)), source_root)
        new_semantics = canonical_none_comparisons(ast.parse("\n".join(out)), source_root)
        if old_semantics == new_semantics:
            return "equivalent", edits
    if path.startswith("sqlglot/") and "exp.alias_(" in patch and "copy=" in patch:
        if fresh_expression_semantics(ast.parse("\n".join(lines))) == fresh_expression_semantics(ast.parse("\n".join(out))):
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
        if path.startswith("sqlglot/generators/") and "self.dialect.version" in patch:
            boundary = version_boundary(original_tree, new, edits)
            if boundary is not None:
                return "version:" + boundary, edits
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
        if without_values(ast.parse("\n".join(out))) == without_values(ast.parse("\n".join(lines))):
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
                keys = numeric_escape_value_edits(original_tree, new, cls, edits)
                if keys:
                    return "token_escape:" + name + ":" + keys, edits
                setting = changed_integer_setting(original_tree, new, cls, edits)
                if setting:
                    return "dialect:" + name + ":" + setting, edits
                return "dialect:" + name, edits
            return "dialect", edits
    return "broad", edits
