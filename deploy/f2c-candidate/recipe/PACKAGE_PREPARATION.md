# Future native Jammy package preparation

This is an implemented preparation path, not evidence that a runner has been launched or a native build has passed.

CLI, on an explicitly authorized Ubuntu 22.04 amd64 runner:

- `/usr/bin/python3 recipe/prepare_jammy_packages.py --root /absolute/fresh/candidate` resolves, downloads and verifies. It does not install. Its output has `installed: false` and cannot satisfy the build guard.
- Add `--install` only for the authorized disposable runner preparation. The script then installs the exact verified transaction and verifies the complete before/after package inventory. It does not publish files, launch workflows or invoke Codex.

Requirements: system Python with Ubuntu's `python3-apt` (native APT 2.4 ABI), `apt-get`, `gpgv`, `dpkg-deb`, a pre-existing trusted Ubuntu archive keyring, and noninteractive sudo only when installation is selected. Missing prerequisites are reported; the script does not bootstrap them from an unknown source. Use `/usr/bin/python3`, not the project's separate Python 3.12 interpreter, for this script.

## Flow

1. Fail on Debian, another Ubuntu release or non-amd64 architecture before creating preparation state.
2. Capture base dpkg status and complete installed inventory. Fetch missing official snapshot metadata into candidate-local storage. InRelease hashes and sizes are pinned in reviewed source. Verify each signature with the runner's trusted keyring before fetching or reading its package indexes; check every index against authenticated SHA256 and size.
3. Build a candidate-local, signed, read-only APT repository from that metadata. All APT configuration, package lists, logs and download caches are candidate-local. No host sources or pinning files are edited and no external repository participates.
4. Use native `apt_pkg` to solve the exact root requests. Walk its actually satisfied Depends/Pre-Depends targets for requested roots, all changed packages and installed Essential packages. Include all already-installed satisfying alternatives conservatively. This is the full relevant package closure, not merely the install delta.
5. An older relevant installed version missing from the fixed snapshot may only be upgraded to an authenticated version and re-solved. Missing provenance, a newer installed version needing downgrade, changed package holds, any removal/downgrade, or an unsatisfied constraint stops preparation. No unrelated blanket system upgrade or autoremove occurs.
6. Download every closure archive from the fixed official snapshot, including already-installed dependencies. Check signed SHA256/size and archive control identity before any installation. Ask APT only for cache naming, stage the independently verified payloads, then re-simulate and compare the exact transaction.
7. Installation uses `--no-remove --no-download`; no downgrade permission is supplied. Recheck dpkg status before installation, verify the complete post-install inventory equals the expected transaction, run APT's consistency check, and compare the pkg-config executable/alias to its authenticated package bytes.
8. Only successful installation produces `installed: true` and `installation_verified: true`. `package-preparation.json` includes candidate-local SHA256 file rows, signed metadata, full closure archives, base/post inventories, solver transaction, logs and provider decisions. The host preparer independently checks this receipt and measures the actual host inputs afterward.

Failure leaves evidence and no successful readiness receipt. Choose a new fresh preparation root after correcting a blocker; the script does not blindly resume or overwrite a partial transaction.

## pkgconf versus pkg-config

The official GitHub Ubuntu2204 inventory read on 2026-10-22 identifies image 20261012.322.1 and lists `pkg-config 0.29.2-1ubuntu3`; it does not establish that every runner has pkgconf. Actual dpkg state is authoritative.

Signed Jammy metadata identifies `pkgconf 1.8.0-1` as providing `pkg-config (= 0.29-1)` and breaking `pkg-config (>= 0.29-1)`. If that authenticated pkgconf provider is already installed alone, the preparer keeps its exact version, records the explicit supported root-provider alias, requires APT to satisfy all actual dependency constraints, and verifies installed executable bytes against its archive. It does not attempt to co-install pkg-config or remove pkgconf. Both providers, an unknown provider/version, wrong executable ownership, incompatible dependencies or any removal proposal cause a clear stop.

Official references:
- https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2204-Readme.md
- https://raw.githubusercontent.com/actions/runner-images/main/images/ubuntu/Ubuntu2204-Readme.md
- https://snapshot.ubuntu.com/
- https://apt-team.pages.debian.net/python-apt/library/apt_pkg.html

## Verification completed here

The stdlib fixture suite covers OS guards, signed metadata parsing, archive path restrictions, bad hashes/oversized downloads, native APT API fixtures, dependency alternatives/cycles, provider conflicts/bytes, exact transaction parsing, holds/removals/downgrades, stale inventory and unexpected post-install changes. The actual Debian-host invocation exits 2 before preparation state exists. This is not a native Jammy execution test. No APT update, package installation or experiment was run here.
# Failure diagnostics (v2)

The first hosted attempt failed during package preparation, but its public
`UNKNOWN` code does not identify a cause. The published v1 script was byte-equal
to the reviewed local script. Its package paragraph delimiter was correct, and
all 118,009 package stanzas in the six previously authenticated indexes parse
successfully. A new real-like multi-stanza regression test exercises the same
index parser used by preparation. No hosted root cause is inferred from timing.

On an exception, preparation now emits a final `SAFE_PACKAGE_DIAGNOSTIC:` line
with a strict nine-field JSON object: schema, code, operation, exception_class,
http_status, errno, returncode, suite, and component. Text values come only from
fixed allowlists; numeric values are bounded. It never serializes exception
messages, URLs, paths, arguments, headers, inventory, or environment values.
`package-failure.json` receives the same diagnostic when possible, with exclusive
creation, no symlink following, and mode 0600. An existing diagnostic is never
overwritten. A diagnostic does not authorize a retry or change an acceptance gate.

The native solver's final diagnostic is validated again against a closed schema
before its code and exception class can appear in the outer diagnostic. No raw
child line is forwarded. The outer return code remains the actual child exit
status. Both system-Python child invocations use `-I -B`; isolated mode does not
disable the system site-packages needed by the distribution's `apt_pkg` module.

The subprocess operation identifies the failing phase, but its bounded exit
code is not the underlying native APT error message. In particular, failure in the
installation phase may have partially changed the disposable runner; preserve
that cohort and use a separately reviewed fresh candidate rather than resuming.

Debian-cloud evidence is not a Jammy runtime test. Python 3.10 syntax is checked
with the parser; Python 3.10 and native `apt_pkg` are unavailable here. The single
clean-environment cloud fetch of the pinned official Jammy InRelease raised
`URLError` without an HTTP status. It was not retried, and does not establish the
cause of the hosted failure. Existing signature, hash, exact-version, no-removal,
and installed-state checks remain in force.

# Provider-selection diagnostics (v3)

The second hosted attempt stopped at `provider-selection` with `RuntimeError`
and `APT_INVARIANT_REJECTED`. This identifies the phase, not the exact rejecting
condition. The v3 delta assigns five finite codes through `ProviderSelectionError`:

- `APT_PROVIDER_COEXISTENCE_REJECTED`
- `APT_PROVIDER_VERSION_UNAUTHENTICATED`
- `APT_PROVIDER_CAPABILITY_MISSING`
- `APT_PROVIDER_OWNER_MISMATCH`
- `APT_PROVIDER_EXECUTABLE_MISSING`

The existing nine-field diagnostic schema is unchanged. No installed versions,
package list, ownership output, file paths, or raw exception messages are exposed.
All five acceptance predicates are unchanged, and no provider is automatically
removed or replaced to bypass the rejection.

The authenticated snapshot contains `pkgconf` 1.8.0-1 with
`Provides: pkg-config (= 0.29-1)` and `Breaks: pkg-config (>= 0.29-1)`.
The pinned `pkg-config` 0.29.2-1ubuntu3 satisfies that Breaks bound. It declares
Conflicts/Replaces only against `pkg-config-bin`; pkgconf declares neither of
those fields. Separate executable names therefore do not justify ignoring the
authenticated Breaks relationship. No authenticated Jammy payload manifest was
available in the local cache; Debian pkgconf archives are not Jammy evidence.

The available evidence does not establish which invariant rejected the hosted
runner. The authenticated pkgconf-only root-selection fixture passes. Retain the
stopped cohorts; a future finite reason code can guide a narrowly supported fix.

# Authenticated pkgconf diversion handling (v4)

The v3 hosted receipt identifies `APT_PROVIDER_OWNER_MISMATCH`. Investigation
demonstrated an ownership-parser defect, without claiming that the unavailable
hosted stdout has been recovered. The signed Jammy `pkgconf` 1.8.0-1 amd64 archive
has size 35280 and SHA256
`257c91d3c0d18e2e0856cb7e86cb4ddf18b91fc40df5bcc45ebaccb25d4e6d37`.
Its preinst registers a pkgconf-owned diversion from `/usr/bin/pkg-config` to
`/usr/bin/pkg-config.real`. Its data archive contains the canonical relative
`/usr/bin/pkg-config -> pkgconf` symlink and the regular `/usr/bin/pkgconf` binary.
The archive and scripts were inspected only as data, never installed or executed.

The [official Jammy dpkg-query manual](https://manpages.ubuntu.com/manpages/jammy/man1/dpkg-query.1.html)
documents diversion records in `-S` output. A read-only query against synthetic
private dpkg metadata on Debian reproduced this exact successful stdout:

    diversion by pkgconf from: /usr/bin/pkg-config
    diversion by pkgconf to: /usr/bin/pkg-config.real
    pkgconf: /usr/bin/pkg-config

This is format evidence, not a Jammy runner test. The v4 parser accepts only one
exact pkgconf or pkgconf:amd64 owner line for the canonical path, optionally with
both exact diversion lines above. Duplicate records, missing halves, other/local
diversions, wrong paths, arbitrary warnings, and unknown text still fail closed.
Only the ownership query separates stderr into a private log; stderr is never
parsed as ownership evidence. Other subprocess behavior is unchanged.

Ownership parsing is provisional. After the complete native closure's archive
hashes and control identities are verified, and before transaction planning or
installation, the existing `verify_provider_member` machinery compares the live
canonical symlink and executable bytes with the authenticated pkgconf archive.
This proof applies to both plain-owner and diversion forms. No preceding step
invokes pkgconf. Post-install verification remains mandatory. A fixture with a
nonempty planned transaction proves that byte mismatch prevents planning/install;
another verifies that archive control mismatch stops before byte verification.
No early payload cache, extra metadata source, removal permission, or diagnostic
schema change was introduced.

# Closure-provenance diagnostics (v5)

The v4 hosted attempt stopped at `closure-provenance` with
`APT_INVARIANT_REJECTED` and `RuntimeError`. It reached native closure resolution,
but this receipt does not distinguish which provenance condition rejected the
selected package state. The post-closure provider byte proof had not yet run.

V5 assigns four finite codes through `ClosureProvenanceError`:

- `APT_CLOSURE_PACKAGE_UNAVAILABLE`: no authenticated candidate has the exact
  selected package name and architecture.
- `APT_CLOSURE_DOWNGRADE_REFUSED`: the existing dpkg comparison did not establish
  that the highest authenticated version is at least the selected version.
- `APT_CLOSURE_PIN_UNRESOLVED`: a missing identity is already an original root
  or a root added by an earlier provenance pass.
- `APT_CLOSURE_NO_PROGRESS`: the existing defensive progress check failed.

The progress check is not reachable from the current caller with a nonempty
missing list if the preceding checks all pass; its direct fixture preserves the
defensive condition. The existing loop is extracted into a helper for focused
tests. Candidate selection, version comparisons, pin constraints, root updates,
and re-resolution behavior are unchanged. There is no automatic downgrade,
removal, foreign-source acceptance, or architecture substitution.

The nine-field receipt remains closed: no package name, installed or candidate
version, private path, or raw solver output is added. Fixtures cover every
rejection, the defensive check, exact authenticated upgrade convergence, index
ordering, architecture mismatch, Architecture: all, and secret non-disclosure.

Read-only inspection rechecked the three pinned release hashes/sizes and all six
signed package-index hashes/sizes and parsed 118,009 stanzas. The indexes contain
exact Architecture: all records, including the pinned libeigen3-dev and
ca-certificates versions. The [official low-level python-apt API](https://apt-team.pages.debian.net/python-apt/library/apt_pkg.html)
defines `Version.arch` as including `all`; the different
[`Package.architecture()` API](https://apt-team.pages.debian.net/python-apt/library/apt.package.html)
reports native architecture for these packages. The solver uses `Version.arch`.
This does not establish an architecture defect or replace a native Jammy test.
No exact hosted package identity or rejecting branch is inferred from the v4
receipt, and no provenance acceptance rule is relaxed.

# Canonical signed-target projection (v6)

The v5 hosted receipt identifies `APT_CLOSURE_DOWNGRADE_REFUSED`, but not the
selected package or comparator status. V6 keeps every acceptance predicate and
the nine-field receipt unchanged. It records the actual final dpkg comparison
return code in the existing `returncode` field. A nonzero value still rejects:
status 1 distinguishes a false comparison from status 2, a comparison error.
No installed version or other arbitrary host string is published.

For this rejection only, the producer selects the same highest authenticated
record already used by the guard. It carries that record's zero-based package
stanza ordinal, suite, and component into an exclusive, mode-0600
`package-target-reference.json`. The exact five keys are `schema`, `snapshot`,
`suite`, `component`, and `stanza`. The sidecar contains no package/version string.
If creation fails or the file already exists, the original failure receipt is
still emitted, without a target marker. A successful write emits
`SAFE_PACKAGE_TARGET_REFERENCE:<sidecar SHA256>` immediately before the terminal
`SAFE_PACKAGE_DIAGNOSTIC:` line. Neither marker contains raw exception text.

The independent, stdlib-only `project_canonical_target.project(root, receipt,
log_path)` returns `{}` on any invalid or unavailable proof. Otherwise its exact
seven keys are `canonical_package`, `canonical_version`,
`canonical_architecture`, `target_snapshot`, `target_suite`, `target_component`,
and `target_stanza`. The workflow must authenticate this helper's source before
calling it. It neither imports the producer nor reads installed state, runs
native commands, or accesses the network.

The helper requires the exact terminal marker pair and matching nine-field
receipt, then binds the sidecar bytes to the adjacent hash. It independently
checks the three previously signature-verified InRelease byte hashes and exact
sizes embedded in the reviewed source. From that pinned signed document it
derives the compressed and uncompressed index SHA256/size entries. It checks
both hashes, the fixed decoded size, the exact package-stanza count, and the
selected ordinal before extracting canonical fields. Paths are fixed beneath
`metadata/snapshot-20261021T000000Z/`; sidecar values cannot select arbitrary
files. Every path component is opened without following symlinks. Input files
must be regular, singly linked, bounded, and stable throughout their read.

The sidecar is at most 512 bytes; only the final 4096 log bytes are read. Indexes
are at most 16 MiB compressed and 64,332,414 bytes decoded. Decoding permits at
most eight complete XZ streams with a shared total-size bound and a 128 MiB
memory limit per stream. Invalid or truncated trailing data is rejected. This
matters for the real jammy main/universe indexes, which each append a valid
32-byte empty XZ stream. Duplicate control/JSON fields are rejected. The fixed
package-name and version bounds are 60 and 54 characters, respectively, with
closed ASCII syntax; architecture is only `amd64` or `all`.

The marker binds a reference to the preserved terminal event and detects a
changed valid ordinal without matching log evidence. It is not remote
attestation: replacing both a reference and its terminal log can select another
authentic public record. Such alteration still cannot inject arbitrary host
strings into canonical output. A canonical target identifies the authenticated
comparison target, not the installed version or proof of a successful build.

Fixtures cover forged or duplicate references, unknown targets, wrong index or
release bytes, canonical-field syntax, aliases and special files, concatenated
XZ bounds, terminal binding, and status 1 versus 2. Read-only private probes also
projected each of the six real retained indexes' final stanzas, confirming counts
6,090 / 58,923 / 21,642 / 5,960 / 20,377 / 5,017 and exact decoded hashes/sizes.
A separate real-index probe projected updates/main stanza 1632 as `libpq-dev`
`14.24-0ubuntu0.22.04.1` `amd64`. These failure references were synthetic; they
do not identify the actual hosted offender. Raw metadata is not published with
the recipe, and no native execution, installation, downgrade, or removal was
performed for these checks.
