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
    # Execute process-pool jobs inline so coverage includes test worker code.
    import concurrent.futures as futures
    import pickle
    class InlinePool(futures.Executor):
        def __init__(self, *args, **kwargs):
            initializer = kwargs.get("initializer")
            if initializer:
                initializer(*kwargs.get("initargs", ()))

        def submit(self, fn, /, *args, **kwargs):
            future = futures.Future()
            try:
                callee, arguments, keywords = pickle.loads(pickle.dumps((fn, args, kwargs)))
                value = callee(*arguments, **keywords)
                future.set_result(pickle.loads(pickle.dumps(value)))
            except Exception as error:
                future.set_exception(error)
            return future
    futures.ProcessPoolExecutor = InlinePool
    prefix = str(root) + "/"
    hits = defaultdict(dict)
    fixtures = defaultdict(int)
    unknown = []
    partial_subtests = set()
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
    from functools import wraps
    from sqlglot import Dialect, Parser, Generator, Tokenizer, exp
    roles = defaultdict(int)
    classes = defaultdict(int)
    token_escapes = defaultdict(int)
    dialect_versions = defaultdict(int)
    original_tokenize = Tokenizer.tokenize
    @wraps(original_tokenize)
    def tokenize(instance, sql):
        if active and "\\" in sql:
            for char in set(escape_prefix.findall(sql)):
                for cls in type(instance).__mro__:
                    if not cls.__module__.startswith("sqlglot."):
                        continue
                    key = cls.__module__.replace(".", "/") + ".py:" + cls.__qualname__
                    token_escapes[key + ":" + char] |= active
        return original_tokenize(instance, sql)
    import re
    escape_prefix = re.compile(r"(?<=\\)([0-7A-Za-z])")
    Tokenizer.tokenize = tokenize
    original_generate = Generator.generate
    @wraps(original_generate)
    def generate(instance, *args, **kwargs):
        version = instance.dialect.version
        if active and isinstance(version, tuple) and all(type(part) is int for part in version):
            key = ",".join(map(str, version))
            dialect_versions[key] |= active
        return original_generate(instance, *args, **kwargs)
    Generator.generate = generate
    def observe_init(cls):
        original = cls.__init__
        @wraps(original)
        def tracked(instance, *args, **kwargs):
            key = type(instance)
            if key not in seen:
                seen.add(key)
                for base in key.__mro__:
                    module = base.__module__
                    if module.startswith("sqlglot."):
                        path = module.replace(".", "/")+".py"
                        roles[path] |= active
                        classes[path + ":" + base.__qualname__] |= active
            original(instance, *args, **kwargs)
        cls.__init__ = tracked
    seen = set()
    for cls in (Dialect, Parser, Generator, Tokenizer, exp.Expr):
        observe_init(cls)
    ids = [test["id"] for test in tests]
    by_class = defaultdict(list)
    for i, name in enumerate(ids):
        by_class[name.rsplit("::", 1)[0]].append((i, name))
    def activate(mask):
        nonlocal active
        active = mask
        seen.clear()
        monitor.restart_events()
    for members in by_class.values():
        targets = []
        positions = {}
        for i, name in members:
            target = name.replace(".py::", "::").replace("/", ".").replace("::", ".")
            loaded = list(unittest.defaultTestLoader.loadTestsFromName(target))
            if len(loaded) != 1 or not isinstance(loaded[0], unittest.TestCase):
                unknown.append(i)
                continue
            targets.append(loaded[0])
            positions[id(loaded[0])] = i
        shared = sum(1 << i for i in positions.values())
        completed = set()
        class Result(unittest.TestResult):
            def startTest(self, test):
                super().startTest(test)
                activate(1 << positions[id(test)])
            def addSubTest(self, test, subtest, err):
                super().addSubTest(test, subtest, err)
                if err is not None:
                    partial_subtests.add(positions[id(test)])
            def addError(self, test, err):
                super().addError(test, err)
                partial_subtests.discard(positions.get(id(test)))
            def addFailure(self, test, err):
                super().addFailure(test, err)
                partial_subtests.discard(positions.get(id(test)))
            def addSuccess(self, test):
                super().addSuccess(test)
                completed.add(positions[id(test)])
            def stopTest(self, test):
                super().stopTest(test)
                activate(shared)
        activate(shared)
        monitor.set_events(tool, monitor.events.LINE)
        result = Result()
        unittest.TestSuite(targets).run(result)
        monitor.set_events(tool, 0)
        active = 0
        unknown.extend(set(positions.values()) - completed)
    out = {
        "token_escapes": {key: hex(mask) for key, mask in token_escapes.items()},
        "dialect_versions": {key: hex(mask) for key, mask in dialect_versions.items()},
        "partial_subtests": sorted(partial_subtests), "classes": {p:hex(m) for p,m in classes.items()}, "roles": {p:hex(m) for p,m in roles.items()}, "ids": ids, "unknown": unknown, "fixtures": {p:hex(m) for p,m in fixtures.items()},
           "lines": {p:{str(n):hex(m) for n,m in rows.items()} for p,rows in hits.items()}}
    with gzip.open(output, "wt") as stream:
        json.dump(out, stream, separators=(",", ":"))
    print(len(ids), "tests;", len(unknown), "incomplete", file=sys.stderr)

if __name__ == "__main__":
    main()
