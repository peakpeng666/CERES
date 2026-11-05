# Compiler-only Docker input materializer candidate

This directory is code plus immutable source/wheel references, not a prepared
image, measured runtime lock, native build or acceptance result. Only local
pure-Python fixture tests have run. No Docker command, image build, package
installation, executable download, native import, application hook or publication
was performed while authoring this candidate.

## Deliberately narrow first stage

The only application inputs are the regular files
`application/src/lab/pyproject.toml` and `application/src/lab/uv.lock`, copied from
exact clean commit `3acf840d1a0c16ab1cfcca15744e4ea6609ddb35`. Their complete
membership and bytes must be anchored in the reviewed source index. Additional
application code, aliases, Git metadata and hooks are refused. These files are
read as data; no application dependency synchronization or application hook runs.

`/inputs/validation-data` must be absent. There is no validation opt-in switch.
Node, npm, frontend dependencies, maps and benchmark datasets are not provisioned
or required here. Full runtime validation remains a separate later workflow.
The lock declares `preparation.scope: compiler-build-only` and
`validation_inputs_prepared: false`; it cannot claim native/full-suite acceptance.

## Fixed layout and bootstrap

- `/inputs`: final materialization path, later exported and mounted read-only.
- `/control`: outside-controller handshake, mounted read-only during workloads.
- `/out/build`: fresh writable compiler outputs; never beneath `/inputs`.
- `/opt/urp-python/bin/python3.12`: image-resident CPython **3.12.13**.
- `/opt/urp-uv/uv`: image-resident **0.12.23** bootstrap.

Use the selected host caller's numeric UID/GID as explicit image build inputs.
The trusted root package/bootstrap stage assigns `/inputs` to those values;
materialization then runs under that nonroot owner. Do not guess a fixed UID.

The toolchain stays inside the image. Ubuntu package provenance must be a signed
complete relevant dependency closure, not transaction delta. Python/UV bootstrap
trust is explicitly different and never represented as signed Ubuntu `.deb`
provenance. Their exact immutable provenance constants are enforced by
`guardlib.BOOTSTRAP_PROVENANCE`.

`/inputs/bootstrap-tools.json` has schema 1 and exactly `tools.python` and
`tools.uv`. Each entry has exactly `version`, `path`, `sha256`, and `provenance`.
Extra tool entries, including Node, are refused. The separate trusted bootstrap
stage authenticates and measures this receipt before materialization.

## Future preparation API

After independent review of the source assembly, finish the separate signed
package/bootstrap stage, stage the two selected application files, and invoke
inside the selected final image environment:

```sh
/opt/urp-python/bin/python3.12 -I -B /inputs/recipe/prepare_inputs.py \
  --root /inputs \
  --python /opt/urp-python/bin/python3.12 \
  --package-lock /inputs/package-preparation.json \
  --expected-source-index-sha256 "$EXTERNALLY_REVIEWED_SOURCE_INDEX_SHA256"
```

`source-index.json` must have schema 1, status `reviewed-source`, exact
`application_commit`, and regular-file SHA256 rows covering `SOURCE_REQUIRED`,
the two selected application files, and assembled controller code. Its SHA256 is
an external review input, never inferred approval from a self-generated file.
`candidate-source-index.json` is intentionally only a candidate awaiting review.

The preparer fetches only already-pinned scientific source/build-wheel archive
bytes, performs bounded data-only extraction, proves selected Ubuntu command
bytes from signed `.deb` payloads before parsing ELF metadata using authenticated
image `readelf`, and creates the narrow OR-Tools interface shim. No downloaded
source, wheel, application hook, native import, package maintainer script or
native build runs. Image `dpkg-deb --fsys-tarfile` only reads package payloads;
its initial trust belongs to the separate authenticated bootstrap.

Outputs preserve existing schema-1 file references and anchor naming:

- `reviewed-inputs.lock.json`: measured image and compiler-input closures.
- `preparation-evidence/runtime-interface.json`: provider/ELF and exact zlib
  alternatives, explicitly not structural-ABI proof.
- `preparation-evidence/system-provider-bytes.json`: Ubuntu command/provider
  archive-byte proof, explicitly excluding Python/UV bootstrap tools.
- `preparation-evidence/python-abi.json`: version, implementation, SOABI and suffix.
- `artifact-index.json` and `prepared-anchor.json.artifact_index_sha256`.

The new lock declares `urp.f2c.docker-engine-netnone/1`, not the historical
same-live-runner namespace contract. This declaration does not prove isolation.
The separate controller/live gate must verify image identity, read-only input and
control mounts, fresh output, network absence and kernel/engine policy.

## Read-only preflight

```sh
/opt/urp-python/bin/python3.12 -I -B /inputs/recipe/verify_prepared_inputs.py \
  --root /inputs --python /opt/urp-python/bin/python3.12 \
  --expected-index-sha256 "$MEASURED_ARTIFACT_INDEX_SHA256"
```

This only verifies bytes, versions and membership and reports
`isolation_verified: false`. It has no execution switch. The new builder may
import `verify_prepared(root, python, lock, guardlib)` after its engine gate.
Historical build/namespace launchers are neither copied nor invoked.

## Pure tests

```sh
python -B -m unittest discover -s recipe/tests -v
```

Fixtures/mocks cover bounded extraction, URL restrictions, signed package-byte
proofs, static ELF/interface parsing, exact zlib alternatives, source anchors,
split Docker layout, Python/UV-only bootstrap, strict selected application files,
and absent validation inputs. They do not execute native/image workloads.
