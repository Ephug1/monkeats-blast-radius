"""Find explicit fixture references in test-method bodies."""
import ast
import json
import sys
from collections import defaultdict
from pathlib import Path

root = Path(sys.argv[1])
tests = json.loads(Path(sys.argv[2]).read_text())["tests"]
fixtures = list((root/"tests/fixtures").rglob("*"))
by_alias = defaultdict(set)
for file in fixtures:
    if file.is_file():
        key = str(file.relative_to(root))
        for alias in (file.stem, file.name, str(file.relative_to(root/"tests/fixtures"))):
            by_alias[alias].add(key)
nodes = {}
for file in {test["file"] for test in tests}:
    path = root/file
    if path.is_file():
        nodes[file] = {n.name:n for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.FunctionDef)}
readers = defaultdict(list)
for test in tests:
    method = nodes.get(test["file"], {}).get(test["id"].split("::")[-1])
    if method:
        paths = set()
        for n in ast.walk(method):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                paths.update(by_alias.get(n.value, ()))
        for path in paths:
            readers[path].append(test["id"])
print(json.dumps(readers,separators=(",",":")))
