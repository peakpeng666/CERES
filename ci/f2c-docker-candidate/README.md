# Docker compiler-stage candidate v3

**Local review candidate, NOT RUN.** No Docker call, image pull, package installation,
native compilation, publication or hosted workflow dispatch was performed while
writing this candidate. Root reviews and authorizes any actual run separately.
This protocol is distinct from the rootless capability v7 experiment and cannot
establish that experiment's PASS.

## Narrow first-stage deliverable

`workflow/compiler-build.yml.example` is a manual-only, pinned-actions example,
not an installed workflow. It prepares an official Ubuntu 22.04 amd64 compiler
image with an authenticated package closure, then runs the retained compiler
plan offline under `urp.f2c.docker-engine-netnone/1`. Native build success writes
`built_not_accepted`, not scientific or deployment acceptance.

Selected application Git-blob provenance (not a full checkout): `3acf840d1a0c16ab1cfcca15744e4ea6609ddb35`.
Interpreter: CPython **3.12.13**, not the system Python 3.10 bootstrap interpreter.
F2C remains 2.1.0; OR-Tools remains 9.15.6755. Upstream source/interface pins are
in `preparation/inputs`. The retained compiler command body has an AST hash
regression and `build-plan-provenance.json`.

The base is the official amd64 platform manifest
`docker.io/library/ubuntu@sha256:98510ac07bb4545e389be502a8918b385c018bfe4ceeb5ac7e0375c782268b04`.
Existing metadata-only verification is retained in `evidence/`. Digest agreement
is content identity, **not a claim of a publisher signature**. Signed Ubuntu APT
snapshot provenance and setup-action/uv Python bootstrap provenance are separate
trust roots, explicitly recorded. See preparation documentation and receipts.

## Local review and later authorized launch

1. Review every candidate source file and run its pure fixtures. These fixtures
   do not invoke Docker, download software or run native scientific code.
2. After review/freeze, run `python3 freeze_source_index.py --root <candidate>`.
   Independently record the printed SHA256. The schema label `reviewed-source`
   is not self-authorization: the external reviewer must approve those bytes.
3. A future authorized workflow pins the candidate Git revision and this source
   index SHA, alongside the separate exact application revision above. The
   example assumes the candidate is stored at `ci/f2c-docker-candidate` in that
   future revision. Nothing has been published there by this work.
4. The workflow invokes `preparation/bootstrap_tools.py collect` after immutable
   setup-uv action, then `preparation/prepare_image.py` with explicit
   `--execute-trusted-preparation`. This networked, trusted stage authenticates
   packages and inputs; it does **not compile project native source**. It exports
   `/inputs`, immutable local image ID, provenance and a complete input index.
5. `engine_controller.py --execute-authorized-run --phase build` consumes that
   exact image ID and independently anchored input-index hash. It never pulls
   an image or repairs host settings. The example budgets a 5 GiB `/out` tmpfs,
   6 GiB memory limit, 2 CPUs, 512 processes and a 3600-second workload deadline.
   This is a finite candidate budget, not a promise compilation fits it.

The example has no automatic retry, cache publication, raw artifact upload,
release, deployment or PR trigger. Only a closed-vocabulary public receipt exposes finite stage outcomes, bounded
exit codes and approved hashes. **Private raw logs are ephemeral and not durably
archived; they disappear with the hosted runner.** Existing private collection
can preserve reviewed sources and the exposed receipt, not unavailable raw logs.
Store any still-accessible evidence privately. Export failures
retain a bounded partial tar when possible and explicit failure records. If the
container was killed before export, vanished tmpfs evidence is reported as
missing rather than fabricated. Cleanup is restricted to the exact new container.

## Isolation identity and trust boundary

The trusted host controller uses only the local Docker engine with an empty
private client config and an allowlisted environment. The workload has no
credentials or Docker socket. It uses the engine-created network-none namespace,
non-root numeric UID/GID, no capabilities, default seccomp, no-new-privileges,
read-only root and inputs, private IPC/cgroup namespace, bounded tmpfs output and
bounded daemon logs. No privileged mode, host network, seccomp-unconfined,
nested-unshare exemption or persistent host security changes are used.

A live entrypoint is held while the controller cross-checks immutable image and
container policy, host PID/start time, kernel net namespace, boot identity and
inside FD/capability/route observations. It stays live through bounded evidence
export. Failure to inspect the live host PID, PID reuse, unexpected mounts,
external routes or unsupported defaults fails closed. The old rootless namespace
receipt is neither reused nor relabeled.

## Explicitly pending acceptance

A compiler-stage zero exit establishes compilation only. A separate runtime image
must be prepared from the same official base with its own authenticated runtime
package closure, without compiler/build-tree dependencies. It must then pass
fresh relocated-venv imports, ELF ABI/GLIBC/RUNPATH/strong-symbol closure, actual
both-order provider mapping, independent geometry and checker evidence, and the
full **current application** suite plus frontend contracts. The runtime adapter is separately reviewable but not first-stage acceptance.
The compiler source index deliberately excludes the pending `validation/` and
`preparation/runtime/` subtrees; they require a later separate freeze/review.

Current application collection/results must be measured afresh. The historical
185 application's 602 testcase elements and 17 additional subtests (619 reported
suite tests) are historical only, not a required or inferred current count.
Any eventual portability statement is scoped to the tested Jammy runtime,
not universal Linux, ROS, physical safety, deterministic timing or bit-identical
build reproducibility.

## Review revision 4

This revision preserves the separate immutable revision-3 snapshot and fixes its
review findings. The controller rejects every unsupported input node using
`lstat`, including mocked FIFO/socket/device cases, and rejects writable input
directories. The independent workflow bootstrap uses explicit checks that remain
active under optimized Python. Missing result evidence or public projection
failure now forces a nonzero workflow result, even after a zero build exit.
Pure tests exercise the actual inline bootstrap and EXIT trap; they do not create
special files, run Docker, install packages or compile native code.

The source-indexed manual workflow remains a review template. A separately
rendered fixed-pin push wrapper is needed for a newly introduced workflow on a
repair branch. Its source and index pins are selected at review/render time, not
by workflow inputs. Root retains publication and execution authority. The
rendered wrapper must live outside this source-indexed tree to avoid a circular
source hash. No compilation, runtime validation or deployment acceptance has yet
been established by these local tests.

## Review revision 5

Revision 5 keeps the immutable v4 snapshot and corrects preparation reads that
mistook access-time updates for source mutation. `prepare_image.read_file` and
`bootstrap_tools.collect` compare device, inode, mode, link count, owner/group,
size, mtime_ns and ctime_ns explicitly; atime is intentionally excluded. Source
anchors, digest rereads, size/type and symlink/hardlink checks remain active.
Owned-file and controlled-stat tests cover atime-only reads, each stable field,
content replacement, inode replacement and post-read symlink substitution. The
UV version command is mocked in these fixtures; no native tool is executed.

The public preparation failure detail is an exact small JSON projection: a fixed
preparation stage, allowlisted invariant code and allowlisted exception class.
Unknown errors receive a fixed generic code. The receipt reader rejects aliases,
oversized input, duplicate keys, extra fields and free text. It does not scan or
publish Docker logs, commands, paths, URLs, headers or environment values. Nested
SAFE_PACKAGE_DIAGNOSTIC extraction is deliberately not implemented in this
revision; image-build failures identify their top-level stage/class only. Raw
logs remain ephemeral, not durably archived.

## Review revision 6: first-run failure observability only

The first hosted attempt stopped during image preparation before native
compilation. This revision adds `--progress=plain` and a bounded projection of
existing safe bootstrap/package markers in the private preparation log. It does
not change package selection, source/input authentication, build commands beyond
progress presentation, isolation or acceptance gates. No dependency workaround
is inferred from the initial generic image-build failure.

`buildkit_failure_detail` is populated only for failed preparation at the
image-build stage. Marker lines must have the expected BuildKit vertex/timestamp
framing and a vertex tied to the exact reviewed Dockerfile RUN command. The
projection uses fixed codes, finite enums and bounded numeric fields only.
Unframed summary replays do not establish a source; a successful bootstrap marker
is not a failure. Missing, oversized, malformed, conflicting or unrecognized
records produce no nested detail. No raw logs, commands, filesystem paths, URLs,
headers, environment values or arbitrary strings are published.

This is structural diagnostic attribution, not cryptographic child identity or
proof of a successful/failed acceptance gate. The original nonzero result and
finite top-level preparation stage remain authoritative for the job outcome.
Raw preparation logs remain ephemeral and are not uploaded or durably archived.

## Review revision 7: bounded pre-RUN log structure

The v6 safe-marker schema is unchanged. A separate `buildkit_structure` is
projected only for failed image preparation. It distinguishes a missing, empty,
unreadable, unsupported, oversized, changed or invalid log from a bounded read.
`log_bytes` is at most 16 MiB + 1; that sentinel with `size_capped: true`
means only that the log exceeds the limit, not its exact size. No oversized log
content is scanned. Each line and the vertex/event counts remain bounded.

`buildkit_frames_seen` means syntactically valid, anchored BuildKit framing was
observed; it is not authenticated process identity. `stages_reached` is a fixed
canonical-order subset of `base_metadata`, `base_pull`, `bootstrap`, `package`,
not a timing sequence. Base stages require the exact pinned official image in
the metadata/FROM header; RUN stages require the existing exact static commands.
A single top-level `#N ERROR:` selects a finite `failed_stage` only when its
vertex was bound to such a header. Unknown vertices/stages, multiple errors or
any repeated/rebound vertex conservatively yield `unknown`. ERROR payload bytes
are never decoded, classified, retained in the receipt or published. Nested
output, failure-summary replays and truncated final fragments cannot establish
headers. Ordinary Docker-client text can yield a nonempty log with no frames,
but is deliberately not classified by its free text.

This is diagnostic structure only. It changes no preparation/build acceptance,
package/source pins or isolation setting, does not infer the initial run's cause,
and does not constitute image, compiler or fresh-runtime acceptance. Raw logs
remain private and ephemeral. v6 is preserved as an immutable predecessor.

## Review revision 8: pinned Mozilla certificate filename compatibility

Hosted run 39852207219 reported `BLOCKED_public_ca_seed`. The compiler Dockerfile
COPY destination and shell SEED directory already agree; this revision does not
change them. The public phase marker does not identify its first failed check.

Read-only retrieval of the already-pinned public Ubuntu CA package verified its
139540-byte size and SHA256
`29ca037f49f7a4b41d67ad72946c2e541f3ae52d8f6ab1495d2af09aa391144b`.
Data-only archive inspection, without package installation or maintainer-script
execution, demonstrates an unavoidable v7 parser failure on these exact bytes:
its 120 regular Mozilla certificate members include
`NetLock_Arany_=Class_Gold=_Főtanúsítvány.crt`. Under LC_ALL=C, GNU tar's default
listing escapes its UTF-8 bytes. The old strict ASCII basename predicate rejects
the member (exit 47 after 66 accepted names), independently of the unobserved
hosted substep. Literal listing alone would still reject its '=' and UTF-8.

The fix explicitly selects literal tar listing and permits only that one exact
additional basename alongside the existing ASCII pattern. It does not permit
general Unicode or '=' names. Prefix, regular-file type, field count, size,
member-count, duplicate and traversal checks remain intact. Selected data is
still read only to stdout and validated as public certificate PEM; no archive
member is extracted onto the system or executed. All signature, signer, fixed
hash/size, signed-index/package identity and subsequent installation gates stay
unchanged, as do base/source pins, execution isolation and workflow semantics.

Against the exact archive, the new parser selects all 120 certificates and every
selected certificate passes the unchanged PEM validator. This is a local
read-only parser reproduction, not a new Docker/bootstrap/native run or compiler
acceptance. Frozen v7 and earlier evidence remain unchanged; only an authorized
future hosted run can establish progress beyond this corrected incompatibility.

## Review revision 9: finite bootstrap verification substeps

Actual v8 hosted run 39853523735 advanced beyond the prior CA stage and reported
`BLOCKED_post_install_verification`; isolated compilation did not run. Source
review and pure transaction/status fixtures found no demonstrated compatibility
bug explaining the new failure. This revision therefore does not change package
selection, status/delta semantics, audit criteria or trust checks speculatively.

The existing diagnostic marker now emits an independently allowlisted fixed
`BLOCKED_<phase>__<substep>` code. Its public schema is unchanged, and historical
coarse codes remain accepted. All ten bootstrap phases have literal substep
labels; no package name, path, URL, command, exception text or environment value
is used to construct a public substep. Post-install labels distinguish status
snapshot/parsing, exact transaction delta, root versions, APT check, dpkg audit,
clean audit output, Python prerequisites, final status snapshot/comparison and
timestamp. Other phases receive similarly bounded operation groups. These are
best-effort diagnostic labels, not a waiver, authenticated process identity or
acceptance evidence. The original nonzero failure remains a failure.

The Python prerequisite expression also replaces three assertions with an
explicit exit-code check of the same Python 3.10, APT 2.4 and default-CA-file
conditions. Mocked executions verify equivalent gating with optimization both
disabled and enabled. The actual hosted command used no -O, so this hardening is
not presented as the reason for its failure or proof of Ubuntu runtime behavior.

A portable regression normalizes only the added substep assignments, the two
failure-format expansions and this exact prerequisite expression, then matches
the entire shell script to frozen v8's SHA256. This guards against accidentally
changing other verification logic. The next authorized run, not local synthetic
tests, must establish the first failing post-install operation. No Docker, full
bootstrap, installation or native build is performed by this revision review.

## Review revision 10: individual Python prerequisite results

Actual v9 run 39856071672 / job 120094610005, source index
`e3d87cd7e48a7968d3fcb8f343c9ba899033ef617748fa8ebe09af3582a59bde`, reached
`BLOCKED_post_install_verification__python_prerequisites`. Earlier post-install
status, delta, root-version, APT-check and dpkg-audit operations therefore passed
in that run. The marker does not distinguish an import exception from a failed
predicate. No source-backed runtime defect is demonstrated by this observation;
compilation, image acceptance and fresh-runtime validation have not occurred.

The prerequisite now runs one source-indexed helper in the same isolated system
Python invocation. Its three imports retain their original order and all occur
before the three original predicates. It preserves their short-circuit order:
Python major/minor equals 3.10, `apt_pkg.VERSION.startswith('2.4.')`, and truthy
`ssl.get_default_verify_paths().cafile`. VERSION denotes APT, not python-apt;
the CA predicate observes an existing resolved default file, not certificate
validity or a TLS handshake. Neither condition is weakened or replaced.

Internal exit codes 61–63 distinguish the sys, apt_pkg and ssl import exceptions;
64/65 distinguish Python-version evaluation failure/mismatch, 66/67 APT-version
evaluation failure/mismatch, and 68/69 default-CA lookup evaluation failure/false
result. The shell maps only these codes to literal substep labels. Zero alone
continues; every other code, including interpreter launch or unexpected failure,
uses `python_execution` and remains a failure. Ordinary exceptions are converted
only to nonzero finite codes, never success. No exception text, module value,
path, command, URL or raw output is added to the public diagnostic schema. Any
child output remains in the same private ephemeral log, not a durable artifact.

Pure fixtures exercise the actual helper with fake imports under normal and
optimized Python, compare success/failure to the previous expression, and check
the extracted shell mapping without executing the provisioning script. These
fixtures do not observe the hosted Ubuntu interpreter. Exact reverse-normalized
shell hashes protect every unrelated gate. A subsequent authorized hosted run
must identify the concrete failing prerequisite; this revision itself performs
no Docker, full bootstrap, installation, native build or publication. v9 remains
unchanged. Primary-source distinctions are recorded separately in
`PYTHON_PREREQUISITE_SOURCES.md`.


## Review revision 11: verified distribution CA default alias

Actual v10 run 39858883912 / job 120104103832 failed at
`BLOCKED_post_install_verification__ca_file_missing`. The system Python imports,
Python 3.10 version and APT 2.4 checks passed; the generated distribution bundle's
presence was not observed. Exact Ubuntu package/source inspection demonstrates
a separate deterministic layout mismatch: OpenSSL's compiled default CAfile is
`/usr/lib/ssl/cert.pem`, while these Jammy packages do not supply that alias.
The ca-certificates package generates `/etc/ssl/certs/ca-certificates.crt`.
Successful package configuration and triggers alone therefore do not satisfy the
existing default-CAfile predicate. See `SYSTEM_CA_SOURCE_EVIDENCE.md` and the
source-indexed package evidence for the exact bytes and attribution limits.

This revision provisions only the missing fixed alias inside the trusted
preparation container, after status/delta/root-version/APT/dpkg-audit gates. It
does not reinstall packages, run extra update-ca-certificates commands, replace
the distribution bundle, substitute the bootstrap seed, import host roots, set
SSL_CERT_FILE/SSL_CERT_DIR or relax the original Python prerequisite. That helper
and its original guard remain byte-identical to v10.

The new source-indexed helper rejects SSL_CERT_* overrides, unexpected compiled
default paths, aliased or unsafe expected directories, unsafe ownership/modes,
empty/symlinked/hardlinked/oversized/changing bundles, and bundles not loadable as
CA certificates by SSL. Reads are bounded and exclude atime from mutation checks.
SSL loads held bundle bytes, never a temporary fixture's absolute alias or an
ambient default trust store. Only an absent alias may be created, with the exact
absolute target `/etc/ssl/certs/ca-certificates.crt`. An already-correct owned alias
is accepted after the same checks; every other existing file/link is rejected
without replacement. The receipt is exclusive-create, not overwriteable.

A bounded private receipt records the derived alias, distribution-bundle hash,
size and CA count. It explicitly labels these as generated/derived artifacts,
not archive-owned files. After the full signed package closure is verified, the
closure binder performs another read-only check of the exact alias, bundle and
receipt and includes the result. Existing command/library archive-owner and byte
verification remains unchanged: no general exemption for unowned system files
was introduced. The input index also freezes the resulting closure receipt.

Public failure reporting remains a closed finite substep vocabulary. Unknown
helper exits fail under `system_ca_unknown`; no path, certificate text, exception,
raw stderr or arbitrary value is projected. A missing distribution bundle remains
a finite failure, never silently replaced or regenerated. No claim is made that
this source correction has already passed the hosted preparation stage.

Pure tests use temporary trees and mocked SSL only, including collision
preservation, guarded alias creation, read-only downstream validation, mutation
and privacy fixtures. Existing verification order is preserved and shell
normalization restores v10, then v9 and v8. No Docker, full bootstrap, trust-store
mutation on the authoring host, installation, native build or publication occurs
in this review. The compiler-only scope, target application 3acf, CPython 3.12.13,
base/package/source pins and isolation protocol are unchanged; fresh-runtime and
full-application acceptance remain pending.

## Revision 12: post-install package failure discrimination

The actual compiler11 run 39861467782 / job120112965565 reached the package stage and rejected preparation with `APT_INVARIANT_REJECTED`, `RuntimeError`, operation `post-install-check`. This establishes progress beyond the bootstrap RUN, not a completed prepared image or isolated compilation. The receipt cannot distinguish status parsing, exact inventory delta, or closure version verification. An ordinary nonzero APT-check exit would be classified as ProcessFailure, not the observed RuntimeError; that ordinary subprocess failure is therefore not supported by this receipt. No specific dependency or package defect has been demonstrated from it.

Revision12 changes diagnostics only. Fixed operations identify the status snapshot, inventory parse, exact delta, closure verification, inventory receipt and APT check. On the original delta RuntimeError, a failure-only projection distinguishes missing entries, unexpected entries, version differences, held-state differences and other differences, in that precedence. It never emits package names, versions, values, paths, URLs or exception text. Multiple differences are intentionally represented by only the first applicable category, not a complete diagnosis. Projection errors fall back to the fixed other category; the original exception is re-raised. A matching-delta diagnostic is reserved for an inconsistent failure observation and does not turn rejection into success.

The original `validate_inventory_delta` equality predicate and all closure, installation, provider, signature, archive and isolation gates are unchanged. There is no retry, package upgrade, removal, exception suppression or alternate trust source. The public safe-marker schema is unchanged; only its exact operation allowlist expands. Pure fixtures cannot identify the hosted failing package or establish prepared-image/native/runtime acceptance.

## Revision 13: bounded signed-index references for a version mismatch

Actual run39865350399 / job120126055207 failed with `APT_INVARIANT_REJECTED`, `RuntimeError`, operation `post-delta-version`. The expected and actual installed-package key sets therefore matched, but at least one raw version differed. The baseline was captured after bootstrap, and status immutability plus transaction-simulation comparisons passed before installation. These facts do not identify the package or justify changing candidate selection, dependency pins, or acceptance.

A separate v2 reference marker supplements only this exact failure. It describes the first sorted mismatching key with a single/multiple count bucket, whether expected state comes from the baseline or transaction, and a nullable observed-matches-baseline boolean. Each baseline/expected/observed identity maps only to an exact authenticated package-index coordinate, or explicitly remains unmapped; an absent baseline is represented separately. No package names, version strings, installed paths, exception text or environment values are emitted. Stanza coordinates are zero-based in the existing package_index_rows enumeration. Duplicate signed records use fixed suite/component/stanza ordering. The diagnostic producer bounds inventory and transaction inputs to4096 entries and candidates per identity to16; reference JSON is capped at2048 bytes. It never changes the original validator, installation or rethrown RuntimeError. Failure to obtain a reference leaves the existing finite failure available; unmapped versions are not promised reconstructible.

The new `SAFE_PACKAGE_TARGET_REFERENCE_V2:` record must precede the unique original failure marker on the same exactly recognized package RUN vertex. Other vertices may interleave. The same vertex may not interpose unrelated output, repeat or conflict with a reference, or attach it to another failure operation. Producer and consumer use one source-indexed closed schema, and the public receipt independently revalidates the optional reference. Invalid reference evidence is rejected, not reprinted. Missing references remain compatible with old receipts; the existing v1 target-reference hash marker is unchanged.

Coordinates are diagnostic claims about already-verified signed metadata, not a new process-authentication channel or proof of installed bytes. The local investigator must resolve them against the pinned signed indices and respect unmapped states. Raw logs remain private and ephemeral. This revision neither establishes image preparation success nor performs compilation, fresh-runtime validation, or current-application acceptance.

### v14 parser correctness and separately reviewed cleanup

The control parser now recognizes empty-initial-value colon headers such as
Conffiles, preserving continuation precedence and strict inventory equality.
See CONTROL_FIELD_SOURCE_EVIDENCE.md for exact archive/source provenance and
reconstructed status limitations. The separately reviewed controller cleanup
and its9 fixtures are included without modification. No native success follows
from these pure regressions; fresh compiler/runtime acceptance remains pending.

### v15 remaining preparation stages

Finite failure diagnostics and exact structural header mapping now cover all
five RUN stages after package preparation. See PREPARATION_STAGE_DIAGNOSTICS.md.
This does not identify the actual compiler14 failure or establish any successful
native/runtime acceptance. Original gates and error/exit behavior are retained.

### v16 rendered BuildKit headers and finite census

Diagnostic binding uses the exact known build arguments to render fixed RUN
names and permits identical header repeats documented by the official producer.
A separate finite census reports unbound candidates without accepting them as
failure details. See BUILDKIT_BINDING_SOURCE_EVIDENCE.md. The lost compiler15
private log cannot be replayed; this is not a claim about its actual root cause.
