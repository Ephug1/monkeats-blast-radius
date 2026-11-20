# Monkeats Blast Radius
An offline test selector for SQLGlot eb11aec4be8cb2122afe713a4816662c3f0fb2fa.

At build time the image retrieves the pinned SQLGlot checkout and measures baseline line coverage, fixture accesses and the class hierarchy of dialect objects used by each test. Child-pool work runs inline during tracing. A prefetched baseline map provides a fallback if the profiler exceeds its time budget. Runtime needs only Python's standard library.

The selector applies each diff to baseline source in memory, compares Python syntax trees and widens the selection for module-level or unrecognized changes. A local edit selects tests that executed the changed lines, cheap tests and tests with incomplete baseline traces. Fixture changes select measured readers and tests with static references. A greedy scheduler evaluates one through eight workers and chooses the least costly schedule.

Run with the official starter kit's Docker flags and /data mount, or set SQLGLOT_ROOT to a pinned checkout and run `python selector.py /path/to/sample`. Evaluate the JSON Lines output with the supplied score.py. The selector never reads sample answers. Source-only analysis can miss indirect effects from introspection or differences between baseline and patched control flow.

The pinned image and test inventory come from the competition starter. The maps are derived from SQLGlot's MIT-licensed tests and code. measure.py regenerates the trace map from a clean checkout and inventory.json. index_fixtures.py generates fixtures.json from that checkout and the official tests.json.
