"""Generate test coverage from a clean SQLGlot checkout and the public test inventory."""
import gzip
import json
import os
import sys
import unittest
from collections import defaultdict
from pathlib import Path

def main():
    root, catalog, output = map(Path, sys.argv[1:])
    root = root.resolve()
    tests = json.loads(catalog.read_text())["tests"]
    output = output.resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))
    # Optional DB access remains unavailable when collecting on a minimal host.
    try:
        import duckdb
    except ImportError:
        import types
        sys.modules["duckdb"] = types.ModuleType("duckdb")
    prefix = str(root) + "/"
    hits = defaultdict(dict)
    fixtures = defaultdict(int)
    unknown = []
    active = 0
    tool = sys.monitoring.COVERAGE_ID
    monitor = sys.monitoring
    def line(code, lineno):
        name = code.co_filename
        if name.startswith(prefix):
            rel = name[len(prefix):]
            rows = hits[rel]
            rows[lineno] = rows.get(lineno, 0) | active
        return monitor.DISABLE
    def opened(event, args):
        if active and event == "open" and isinstance(args[0], str) and "fixtures/" in args[0]:
            rel = os.path.relpath(args[0], root)
            fixtures[rel] |= active
    sys.addaudithook(opened)
    monitor.use_tool_id(tool, "test coverage")
    monitor.register_callback(tool, monitor.events.LINE, line)
    ids = []
    for test in tests:
        ids.append(test["id"])
        target = test["id"].replace(".py::", "::").replace("/", ".").replace("::", ".")
        suite = unittest.defaultTestLoader.loadTestsFromName(target)
        active = 1 << (len(ids)-1)
        monitor.restart_events()
        monitor.set_events(tool, monitor.events.LINE)
        result = unittest.TestResult()
        suite.run(result)
        monitor.set_events(tool, 0)
        active = 0
        if result.errors or result.failures or result.skipped:
            unknown.append(len(ids)-1)
    out = {"ids": ids, "unknown": unknown, "fixtures": {p:hex(m) for p,m in fixtures.items()},
           "lines": {p:{str(n):hex(m) for n,m in rows.items()} for p,rows in hits.items()}}
    with gzip.open(output, "wt") as stream:
        json.dump(out, stream, separators=(",", ":"))
    print(len(ids), "tests;", len(unknown), "incomplete", file=sys.stderr)

if __name__ == "__main__":
    main()
