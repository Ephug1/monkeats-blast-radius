"""Select tests from baseline execution traces and fixture dependencies."""
import gzip
import json
import re
import sys
from pathlib import Path
from scheduler import schedule

HERE = Path(__file__).parent
HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")
def changes(patch):
    files = {}
    path = None
    line = 0
    for row in patch.splitlines():
        if row.startswith("diff --git "):
            path = None
        elif row.startswith("+++ b/"):
            path = row[6:]
            files.setdefault(path, [])
        elif row.startswith("@@ "):
            match = HUNK.match(row)
            if not match:
                return {}
            line = int(match[1])
        elif path and row.startswith(("+", "-")):
            files[path].append((line, row))
            line += row[0] == "-"
        elif row.startswith(" "):
            line += 1
    return files

def select(files, index):
    full = (1 << len(index["ids"])) - 1
    if not files:
        return full
    mask = 0
    for path, edits in files.items():
        if not edits:
            return full
        if path.startswith("tests/fixtures/"):
            readers = index["fixtures"].get(path)
            if readers:
                mask |= int(readers, 16)
            else:
                mask |= index["static"].get(path, index["incomplete"])
            continue
        if not path.endswith(".py"):
            return full
        if all(row[1:].lstrip().startswith("#") and n > 2 and n not in index["strings"].get(path, ()) for n,row in edits):
            continue
        rows = index["lines"].get(path, {})
        removed = [n for n,row in edits if row.startswith("-")]
        positions = removed or [neighbor for n,_ in edits for neighbor in (n-1,n)]
        selected = 0
        for n in positions:
            selected |= int(rows.get(str(n), "0"), 16)
        if not selected:
            return full
        mask |= selected | index["incomplete"]
    return mask

def main():
    data = Path(sys.argv[1]) if len(sys.argv)>1 else Path("/data")
    tests = json.loads((data/"tests.json").read_text())["tests"]
    with gzip.open(HERE/"map.json.gz","rt") as stream:
        index = json.load(stream)
    index["strings"] = json.loads((HERE/"strings.json").read_text())
    index["incomplete"] = sum(1 << i for i in index["unknown"])
    positions = {name:i for i,name in enumerate(index["ids"])}
    refs = json.loads((HERE/"fixtures.json").read_text())
    index["static"] = {p:sum(1 << positions[name] for name in names if name in positions) for p,names in refs.items()}
    cache = {}
    for patch in sorted((data/"commits").glob("*.patch")):
        mask = select(changes(patch.read_text(errors="replace")), index)
        if mask not in cache:
            durations = {t["number"]:t["seconds"] for t in tests if t["id"] not in positions or (mask >> positions[t["id"]]) & 1}
            cache[mask] = schedule(durations)
        print(json.dumps({"commit":patch.stem,"workers":cache[mask]}, separators=(",",":")))

if __name__ == "__main__":
    main()
