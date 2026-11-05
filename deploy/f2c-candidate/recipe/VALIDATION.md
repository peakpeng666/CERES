# Relocated Jammy candidate validation

Prepared workflow only. No Jammy native build or validation was executed while authoring these helpers. The prior Debian13 result is not evidence of Ubuntu22.04 compatibility.

## Input and command contract

1. `prepare_validation_data.py --source APP --root FRESH_DATA --python PY31213 --uv UV01223` materializes locked Lab dependencies plus hash-fixed official maps and the350-field archive. It does not install Fields2Cover. Its full receipt/log remain private.
2. After the reviewed build succeeds, `freeze_validation_config.py --source APP --root CANDIDATE --data FRESH_DATA --output FRESH_RELOCATED_OUTPUT --config CONFIG --python PY31213 --node NODE` checks the exact clean application185 commit, official input hashes/materialization receipt, new wheel identity, and snapshots all relevant source/dependency/data trees. It prints the new config SHA256. Output must be outside the build candidate and source/input trees.
3. Launch the reviewed validator in an empty environment, an actual fresh `unshare --user --map-root-user --net` namespace, and the absolute measured CPython3.12.13 interpreter with `-I -B`: `validate_jammy_candidate.py prepare --config CONFIG --expected-config-sha256 SHA`.
4. Only if preparation and complete static ELF checks pass, run the same launcher with `run --config CONFIG --expected-config-sha256 SHA --allow-native-execution`.

The public workflow must anchor helper bytes before the initial Python invocation and redirect full preparation/build/validation output to private files. Never upload raw logs, binaries, source snapshots, maps, traces or JUnit files to public Actions artifacts. No native execution is authorized by the existence of this document.

## Gates

- Full anchored build input verification runs in an exact base-Python child via verify_validation_build.py, without the build-only output-absence requirement. The relocated copied interpreter is separately byte-checked; the builder identity guard is not weakened. Actual namespace topology is verified, not an inherited environment marker.
- Fresh output is non-symlinked and owned; no resumption or overwrite. Source copies preserve symlinks only within the copied source closure. The historical manifest is preserved and only the isolated copy receives a distinct candidate manifest.
- Venv uses the exact measured interpreter and copies frozen dependency bytes. No pip/installer code is needed to install the eight-file candidate wheel; safe ZIP extraction rejects traversal, duplicates, foreign paths and special entries.
- Clean environment has no `LD_LIBRARY_PATH`, `LD_PRELOAD`, Python home overrides or inherited pytest settings. The new extension must have exactly `$ORIGIN/../ortools/.libs:$ORIGIN/../ortools.libs` RUNPATH and Jammy GDAL30/TinyXML9 dependencies.
- Before loading, static `readelf` review walks all installed native ELF files, every native extension/library under the locked CPython prefix (including stdlib lib-dynload consumers), and their transitive providers. Every dependency must resolve within the relocated site, measured interpreter prefix or system library directories derived from the measured runtime-interface provider evidence, including non-multiarch paths such as /usr/lib when actually present. Competing SONAME providers, missing version definitions or unresolved strong dynamic symbols fail closed, except the single explicit libz.so.1 policy below. No `ldd` or downloaded interpreter is invoked for this inspection.
- Both native import orders run separately, then inspect actual process mappings. Exactly one OR-Tools native provider must be the relocated official9.15 wheel library with the fixed SHA256. The mapped F2C extension and all recognized provider paths/bytes must agree with static review. Deleted mappings fail. Provider counts use actual ELF SONAMEs, not only filenames.
- The unchanged mixed-native smoke, exactly three geometry cases and exactly602 full Lab cases must pass with zero skips/errors/failures. Tests are never altered or skipped to fit the environment.
- Final original/copied/installed plus frozen-build/harness checks run even after a failed stage; success requires all checks and the overall process exit to pass.

## Scope and outputs

The application source is commit18557cfc61f291e8ac5fa4a80d9df212e1df0030. Tracked Lab tests, research, frontend core, robotics, backend, deployment fixtures and package data are copied. Generated frontend/dist is absent from that commit and is not manufactured or claimed validated. The602-case Lab gate remains unchanged; a failure on the real runner is evidence to investigate, not permission to reduce this gate.

`OUTPUT/evidence` retains private full logs, JUnit, static ELF closure, process mapping hashes, exit codes and post-run integrity. Only `OUTPUT/public-summary.json` is designed for bounded public reporting, and is written after successful final integrity checks. It reports counts and the candidate wheel hash, not paths or raw source/test data. Passing remains candidate validation, not production acceptance.

The static strong-symbol check is deliberately conservative. If a platform-specific optional loader pattern cannot be proven by this review, stop with private evidence and review a narrow change independently; never bypass the gate automatically.

## Explicit libz.so.1 alternate-provider contract

The official OR-Tools wheel and authenticated Jammy system library both supply the same libz.so.1 SONAME. GDAL and Python startup may load the system provider first, while the OR-Tools wheel RUNPATH may select the wheel provider first. This is not RPATH isolation. CMake continues to link the exact wheel-owned zlib; runtime provenance expressly permits either of exactly two measured providers.

The frozen runtime-interface evidence records those two paths/hashes and the system provider is included in authenticated package-byte/host-file verification. Validation additionally checks both libraries against the union of all required zlib version definitions and strong symbol-version pairs, including default exports for unversioned references. Both must satisfy the complete measured requirement; an incompatible alternative is a hard failure, not a fallback.

Each fresh import-order process must map exactly one of those two approved providers and records its actual path/hash. Zero, two, changed bytes or a third provider fail. The exact wheel-owned libortools provider remains mandatory and unique. No other SONAME gets an alternate-provider exemption. No LD_LIBRARY_PATH or preload override is introduced.
