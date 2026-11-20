"""Index multiline string spans in the pinned Python source."""
import json
import sys
import tokenize
from pathlib import Path

root = Path(sys.argv[1])
result = {}
for folder in ("tests","sqlglot"):
    for path in (root/folder).rglob("*.py"):
        spans = set()
        with path.open("rb") as stream:
            for token in tokenize.tokenize(stream.readline):
                if token.type == tokenize.STRING:
                    spans.update(range(token.start[0], token.end[0]+1) if token.end[0]>token.start[0] else ())
        if spans:
            result[str(path.relative_to(root))] = sorted(spans)
print(json.dumps(result,separators=(",",":")))
