# Monkeats Blast Radius

An offline test selector for SQLGlot at eb11aec4be8cb2122afe713a4816662c3f0fb2fa.

The profiler collects line coverage, file reads and per-test dialect and expression class usage. It runs the pinned unittest cases in class groups to share class fixtures. Its inline process pool pickles arguments and results to exercise the same SQLGlot serialization functions that a subprocess would use. The index contains baseline observations only.

At run time the selector compares the baseline and patched Python ASTs. Local edits use line coverage, literal edits in dialects and expression classes use class usage, and fixture changes select file readers. Incomplete baseline runs use broader module coverage. Partial subtest traces, dynamic imports and reflection can cause the selector to miss a failure. A scheduler compares up to eight worker counts using recorded test durations.

The Docker build tries to generate a Linux profile and uses the bundled fallback if profiling exceeds the build budget. To regenerate the fallback on a machine without DuckDB, place profile_fallback.py on a separate Python import path under the name duckdb.py. This accepts schema setup statements and raises on database queries. Run measure.py with the pinned source directory, inventory.json and the output path. No code from the modified checkout is executed by the selector.

For local evaluation set SQLGLOT_ROOT to the pinned checkout and run python selector.py with the official sample data directory. Pass the resulting JSON Lines file to the official score.py program. Sample labels are used only by the evaluator.
