# Monkeats Blast Radius
An offline test selector for SQLGlot eb11aec4be8cb2122afe713a4816662c3f0fb2fa.

The numbered inventory is matched by pytest node ID. Baseline line execution and fixture reads are measured once using Python's monitoring and audit APIs. Static fixture references supplement the trace. For source edits with measured coverage the selector includes incomplete traces as a precaution. Changes without coverage get the full suite. The scheduler tries one through eight workers and chooses the lowest estimated bill. Sample labels are used only by the external evaluator.

## Run
Build with `docker build --platform linux/amd64 -t blast-radius .`. Mount the scorer input at /data, or run `python3 selector.py /path/to/sample` directly. Output is compact JSON Lines.

## Rebuild the maps
With a clean checkout at the pinned SHA and the official test inventory, run the following (Python 3.12+ for measurement). The project uses Python's standard library. The public map was generated on macOS with pandas, numpy and python-dateutil available. Missing optional dependencies cause incomplete traces. Multiprocess work may also be absent from a parent trace.
```sh
python3 measure.py /path/to/sqlglot /path/to/tests.json map.json.gz
python3 index_strings.py /path/to/sqlglot > strings.json
python3 index_fixtures.py /path/to/sqlglot /path/to/tests.json > fixtures.json
```
The competition starter supplies the pinned Python image and input/output format. The dependency maps are derived from SQLGlot's MIT-licensed source and tests. The submitted selector uses no per-patch answer table.
