# Deferred serving integration

`test_evolve_serve.py` is preserved byte-for-byte from snapshot
`516569d4d03b02c53018cf9866f9ffd241c7c7e8`. Its eight tests exercise the
coverage recommender integration, which is not part of the offline-core slice.
The active `tests/` suite therefore does not collect this file. This is an explicit
unimplemented integration boundary, not a passed or silently skipped test suite.

Before enabling coverage serving, integrate against the current recommender and
independently review its model and scope gates. Move this file into `tests/` and
run it alongside the complete lab suite. Do not enable serving or copy historical
model files merely to satisfy these tests.
