"""Select tests from pinned-commit coverage, fixture dependencies and syntax."""
import gzip
import json
import os
import re
import sys
from pathlib import Path
from scheduler import schedule
from structure import inspect_patch

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
        if kind == "dialect":
            roles = index.get("roles", {}).get(path)
            if not roles:
                return full
            selected |= int(roles,16) | index["cheap"] | index["incomplete"]
            for mask in index["lines"].get(path,{}).values():
                selected |= int(mask,16)
            continue
        if kind == "broad":
            return full
        rows = index["lines"].get(path,{})
        hits = 0
        for pos in edits:
            hits |= int(rows.get(str(pos),"0"),16)
        selected |= hits | index["incomplete"] | index["cheap"]
    return selected

def main():
    data = Path(sys.argv[1]) if len(sys.argv)>1 else Path("/data")
    tests = json.loads((data/"tests.json").read_text())["tests"]
    with gzip.open(HERE/"map.json.gz","rt") as stream:
        index = json.load(stream)
    positions = {name:i for i,name in enumerate(index["ids"])}
    index["incomplete"] = sum(1 << i for i in index["unknown"])
    index["cheap"] = sum(1 << positions[t["id"]] for t in tests if t["seconds"] <= 0.005 and t["id"] in positions)
    refs = json.loads((HERE/"fixtures.json").read_text())
    index["static"] = {p:sum(1 << positions[name] for name in names if name in positions) for p,names in refs.items()}
    cache = {}
    for patch in sorted((data/"commits").glob("*.patch")):
        mask = select(patch.read_text(errors="replace"), index)
        if mask not in cache:
            durations = {t["number"]:t["seconds"] for t in tests if t["id"] not in positions or (mask >> positions[t["id"]]) & 1}
            cache[mask] = schedule(durations)
        print(json.dumps({"commit":patch.stem,"workers":cache[mask]}, separators=(",",":")))

if __name__ == "__main__":
    main()
