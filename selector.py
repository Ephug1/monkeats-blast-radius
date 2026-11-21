"""Select tests from pinned-commit coverage, fixture dependencies and syntax."""
import gzip
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from scheduler import schedule
from structure import inspect_patch, setting_reads

HERE = Path(__file__).parent
SOURCE = Path(os.environ.get("SQLGLOT_ROOT", "/src"))
FILE = re.compile(r"^\+\+\+ b/([^\t\n]+)", re.M)

def select(patch, index):
    full = (1 << len(index["ids"])) - 1
    selected = 0
    sections = re.split(r"(?m)^diff --git ", patch)
    for section in sections[1:] or sections:
        match = FILE.search(section)
        if not match:
            return full
        path = match[1]
        if path.startswith("tests/fixtures/"):
            readers = int(index["fixtures"].get(path, "0"), 16) | index["static"].get(path, 0)
            selected |= readers or full
            continue
        if not path.endswith(".py"):
            return full
        kind, edits = inspect_patch(SOURCE, section, path)
        if kind == "equivalent":
            continue
        # Partial traces retain useful evidence about modules visited before
        # setup or an external integration failed. Unloaded tests stay broad.
        observed = index["module_hits"].get(path, 0)
        unknown = index["unloaded"] | (index["incomplete"] & observed)
        if len(sections) <= 2:
            unknown &= ~index["partial"]
        if kind.startswith("expression:"):
            name = kind.partition(":")[2]
            roles = index.get("classes", {}).get(path + ":" + name)
            if roles is None:
                return full
            selected |= int(roles, 16) | unknown
            continue
        if kind.startswith("token_escape:"):
            name, _, keys = kind.partition(":")[2].partition(":")
            class_key = path + ":" + name
            roles = int(index.get("classes", {}).get(class_key, "0"), 16)
            prefix = class_key + ":"
            escapes = index.get("token_escapes", {})
            if roles and any(key.startswith(prefix) for key in escapes):
                for char in keys:
                    chars = "01234567" if char == "0" else char + char.swapcase()
                    for prefix_char in chars:
                        selected |= int(escapes.get(prefix + prefix_char, "0"), 16)
                selected |= unknown
                continue
            kind = "dialect:" + name
        if kind.startswith("dialect"):
            name, _, setting = kind.partition(":")[2].partition(":")
            roles = index.get("classes", {}).get(path + ":" + name)
            if roles is not None:
                mask = int(roles, 16)
                if setting:
                    reads = setting_reads(SOURCE, setting)
                    direct = 0
                    for file, line in reads:
                        direct |= int(index["lines"].get(file, {}).get(str(line), "0"), 16)
                    if direct & mask:
                        mask &= direct
                selected |= mask | unknown
                continue
            roles = index.get("roles", {}).get(path)
            if not roles:
                return full
            selected |= int(roles,16) | unknown
            for mask in index["lines"].get(path,{}).values():
                selected |= int(mask,16)
            continue
        if kind == "broad":
            return full
        rows = index["lines"].get(path,{})
        hits = 0
        for pos in edits:
            hits |= int(rows.get(str(pos),"0"),16)
        if kind.startswith("version:") and index.get("dialect_versions"):
            version = kind.partition(":")[2]
            hits &= int(index["dialect_versions"].get(version, "0"), 16)
        selected |= hits | unknown
    return selected

def replay_small_selection(patch, tests, durations):
    """Remove passing unittest cases after a bounded isolated replay."""
    total = sum(durations.values())
    if not durations or total > 1.0 or len(durations) > 48:
        return durations
    if len(durations) < 3 and total < 0.15:
        return durations
    paths = FILE.findall(patch)
    if (not paths or patch.count("diff --git ") != len(paths)
            or any(not path.startswith("sqlglot/") or not path.endswith(".py") for path in paths)):
        return durations
    chosen = {str(test["number"]): test["id"] for test in tests
              if test["number"] in durations}
    try:
        run = subprocess.run(
            [sys.executable, "-B", __file__, "--replay"],
            input=json.dumps([patch, chosen]), text=True,
            capture_output=True, timeout=1.8,
        )
        if run.returncode or not run.stdout.startswith("MONKEATS_REPLAY "):
            return durations
        passed = json.loads(run.stdout.removeprefix("MONKEATS_REPLAY "))
        return {number: duration for number, duration in durations.items()
                if str(number) not in passed}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return durations

def replay_worker():
    import importlib.abc
    import importlib.machinery
    import unittest
    from contextlib import redirect_stdout
    from structure import HEADER

    patch, chosen = json.load(sys.stdin)
    root = SOURCE.resolve()
    overrides = {}
    for section in re.split(r"(?m)^diff --git ", patch)[1:]:
        path = FILE.search(section)[1]
        origin = (root / path).resolve()
        if root not in origin.parents:
            return
        original = origin.read_text().splitlines()
        updated = []
        cursor = 0
        started = False
        for line in section.splitlines():
            if line.startswith("@@ "):
                header = HEADER.match(line)
                if header is None:
                    return
                begin = int(header[1]) - (header[2] != "0")
                if begin < cursor or begin > len(original):
                    return
                updated.extend(original[cursor:begin])
                cursor = begin
                started = True
            elif started and line.startswith((" ", "-")):
                if cursor >= len(original) or line[1:] != original[cursor]:
                    return
                if line.startswith(" "):
                    updated.append(original[cursor])
                cursor += 1
            elif started and line.startswith("+"):
                updated.append(line[1:])
        if not started:
            return
        updated.extend(original[cursor:])
        overrides[origin] = compile("\n".join(updated) + "\n", str(origin), "exec")

    class Overlay(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname != "sqlglot" and not fullname.startswith("sqlglot."):
                return None
            rel = fullname.replace(".", "/")
            for origin in (root / (rel + ".py"), root / rel / "__init__.py"):
                if origin not in overrides:
                    continue
                loader = importlib.machinery.SourceFileLoader(fullname, str(origin))
                loader.get_code = lambda name: overrides[origin]
                return importlib.util.spec_from_file_location(fullname, origin, loader=loader)
            return None

    sys.path.insert(0, str(root))
    sys.meta_path.insert(0, Overlay())
    os.chdir(root)
    passed = []
    with redirect_stdout(sys.stderr):
        for number, case in chosen.items():
            parts = case.split("::")
            if len(parts) != 3 or not parts[0].endswith(".py"):
                continue
            name = ".".join([parts[0][:-3].replace("/", "."), *parts[1:]])
            suite = unittest.defaultTestLoader.loadTestsFromName(name)
            result = unittest.TestResult()
            suite.run(result)
            if result.testsRun == 1 and result.wasSuccessful() and not result.skipped:
                passed.append(number)
    print("MONKEATS_REPLAY " + json.dumps(passed))

def main():
    data = Path(sys.argv[1]) if len(sys.argv)>1 else Path("/data")
    tests = json.loads((data/"tests.json").read_text())["tests"]
    with gzip.open(HERE/"map.json.gz","rt") as stream:
        index = json.load(stream)
    positions = {name:i for i,name in enumerate(index["ids"])}
    index["incomplete"] = sum(1 << i for i in index["unknown"])
    index["partial"] = sum(1 << i for i in index.get("partial_subtests", ()))
    index["module_hits"] = {}
    for path, rows in index["lines"].items():
        mask = int(index.get("roles", {}).get(path, "0"), 16)
        for value in set(rows.values()):
            mask |= int(value, 16)
        index["module_hits"][path] = mask
    index["unloaded"] = 0
    for i in index["unknown"]:
        path = index["ids"][i].split("::")[0]
        if not (index["module_hits"].get(path, 0) & (1 << i)):
            index["unloaded"] |= 1 << i
    refs = json.loads((HERE/"fixtures.json").read_text())
    index["static"] = {p:sum(1 << positions[name] for name in names if name in positions) for p,names in refs.items()}
    cache = {}
    for patch in sorted((data/"commits").glob("*.patch")):
        contents = patch.read_text(errors="replace")
        mask = select(contents, index)
        if mask not in cache:
            cache[mask] = {t["number"]:t["seconds"] for t in tests if t["id"] not in positions or (mask >> positions[t["id"]]) & 1}
        durations = replay_small_selection(contents, tests, cache[mask])
        print(json.dumps({"commit":patch.stem,"workers":schedule(durations)}, separators=(",",":")))

if __name__ == "__main__":
    if sys.argv[1:] == ["--replay"]:
        replay_worker()
    else:
        main()
