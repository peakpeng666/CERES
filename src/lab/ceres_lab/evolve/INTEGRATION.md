# Offline evolve core integration

Source: `local/ceres-snapshot-20261023`, commit
`516569d4d03b02c53018cf9866f9ffd241c7c7e8`.
Integration base: main `5ddbd22f0e176b9fb6f0ed7f1eeaa8063ea1b4cb`.

This slice imports the 30 evolve Python modules and 14 historical offline test modules, plus a new
isolation regression module and scoped pytest fixture.
The eight serving/recommender tests are retained separately in
`../../deferred_tests/test_evolve_serve.py`; see that directory's README.
No recommender, frontend, default model, historical result, or research protocol
is replaced. The package is not connected to the live recommendation path.

Current CI collects the active evolve contracts through the complete Lab suite
in `.github/workflows/ci.yml` (`agriculture-contracts`), with the pinned official
maps required. The old dedicated `evolve-research-contracts` job is historical;
do not copy it back alongside the full Lab gate. The eight deferred tests below
are still outside normal discovery. The dated verification sections retain the
original standalone-job and cloud results.

Run from `src/lab`:

```sh
uv sync --locked --inexact
uv run --no-sync python -m pytest -q tests -o 'python_files=test_evolve_*.py' -rs
```

These are synthetic, numerical and geometry contracts. Tests requiring optional
Fields2Cover or historical audit results report their existing explicit skip
reasons. Every active evolve test receives a fresh empty temporary field cache
with independent memoization and downloads blocked, even on a populated host.
Real-field checks therefore skip explicitly rather than reading the host cache. No
benchmark dataset is downloaded or sealed-test field evaluated by this slice.
Passing contracts do not establish research replication, promotion of a learned
policy, a frozen primary endpoint, or ROS/Gazebo/physical acceptance. Do not run
research-generating CLI commands as part of installation or this CI job.

## Cloud verification, 2026-10-24

- Locked lab dependencies installed successfully with Python 3.12.14; Node 24.22.1.
- Evolve discovery: **61 passed, 7 skipped** in 5.92 s, across all 14 historical active files (before the isolation hardening below).
  Five skips require Fields2Cover; one requires the Fields2Benchmark cache;
  one requires historical audit inputs. The eight deferred serving tests were not run.
- Existing agriculture import/provenance tests: **12 passed**.
- Complete lab discovery: **786 passed, 17 failed, 14 skipped**, with 36 passing
  subtests. All 17 failures concern absent official map-backed scenarios
  (`maze-inspection`, `farm-rows-warehouse`, `field-coverage-depot`,
  `p2p-maze-cross`) or their HTTP status expectations. This checkout has no
  ROS map assets. This is **not** a full lab pass; assertions were not changed.
- All 45 imported Python files (30 source + 15 test files including deferred
  tests) match the snapshot's Git blob hashes. No source algorithm was retuned. The subsequent access-guard hardening below
  intentionally changes only `instances.py` among those 45 files.
- Removing the newly added CI job reproduces main's workflow byte-for-byte.
  YAML parsing and `git diff --check` pass.

Local JUnit and console evidence is retained under repository `.local-state/`:
`evolve-research-contracts.xml`, `evolve-tests.log`, `lab-regression.xml`,
`lab-regression.log`. CI uploads the evolve JUnit file. Local evidence is not
claimed as a remote CI run.

## Review hardening

The snapshot token check was only an advisory text marker/prefix check, not
verified protocol-freeze enforcement. Sealed builders now reject every token
before calling field geometry access. `sealed_token()` remains a legacy advisory
digest helper and cannot unlock execution. A future sealed evaluation requires
an independently reviewed authorization gate; no protocol source or approved
freeze is invented by this change.

Intentional deviations from snapshot source:
- `instances.py`: honest guard documentation and fail-closed sealed builders.
- New `tests/conftest.py`: for evolve tests only, replace DIR/INDEX and memoized
  field readers with isolated temporary-cache versions; block field downloads.
- New `test_evolve_isolation.py`: empty-cache/no-projection/no-download checks and
  four forged/empty token cases proving both sealed builders stop before geometry.
- Historical 15 test files remain byte-identical, with the serving file relocated.

After this hardening the full evolve discovery is **67 passed, 7 skipped** in
4.29 s across 15 active files. The broader 786/17/14 lab result above predates
this hardening and is not represented as a final full-suite pass.


## Offline serving guard preparation (separate follow-on slice)

This change does not connect serving to the gateway, recommender or UI, install
any model/ledger, or alter solver defaults. All eight live-integration tests in
`deferred_tests/` remain deferred. The historical model is not an approved model.

`serve.recommend` now requires an explicit in-process `ModelApproval` containing
an operator-reviewed full-document SHA-256, separately reviewed development
membership digest, and review reference. There is no configured production
approval. Constructing the object or matching an artifact's self-declared hash
is not evidence that review occurred. The document must separately carry matching
development provenance; these are identity bindings, not a replacement for an
actual membership audit. No frozen-protocol substring is trusted.

Only static experience orders can pass this offline inspection. Learned serving
is unconditionally disabled pending independently reviewable training membership
and promotion evidence. Output explicitly says static, not learned, and not
deployed. Historical evaluation summaries are not presented as verified claims.

Model cache identity binds resolved path and content bytes/hash, not mtime;
returned objects cannot mutate the cache. Validation rejects malformed dimensions,
non-finite values, invalid order permutations, and unregistered configuration
parameters. Machine checks include robot width. Static orders must pass the same
nearest-neighbour and feature-range checks and cannot silently extrapolate.

The new tests use only generated rectangles and temporary JSON artifacts with
explicit synthetic review identities. They cover missing approval/provenance,
tampering, mismatched membership, unqualified learned kinds, robot width, static
out-of-range inputs, equal-mtime path/content replacements, invalid artifacts,
and returned-object cache mutation. They do not authenticate or approve any
historical artifact or touch sealed datasets.

Final cloud check for this follow-on slice: **83 passed, 7 explicitly skipped**
(all evolve tests); the new serving-guard module contributes **16 passing tests**.
The seven optional-data/runtime skips retain their stated reasons. Gateway,
recommender, existing CI and eight deferred integration tests match the PR12 base
byte-for-byte. `git diff --check` passes. This is offline contract verification,
not live serving, research validation or physical acceptance.
