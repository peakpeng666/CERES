# Signed, Python-free Ubuntu bootstrap candidate

Status: authored for future review only. No Docker command, image pull/build/run,
CA-input download, APT transaction, package installation, native build, external
write/publication, or host mutation was performed. Pure syntax/fixture tests pass;
this is not measured image readiness or native acceptance.

## Purpose and trust boundary

`Dockerfile.bootstrap` starts only from the reviewed linux/amd64 Ubuntu 22.04
platform manifest `sha256:98510ac07bb4545e389be502a8918b385c018bfe4ceeb5ac7e0375c782268b04`.
The external trusted controller must independently bind that pull's observed
manifest/config/platform and the reviewed provisioning files to its evidence.
The script's expected digest string is not evidence of actual image identity.

The pinned minimal image does not need Python, python3-apt, curl, wget, an xz
executable, gpg, openssl, or installed CA certificates to begin. It does need the
base image's native APT 2.4, apt-helper, dpkg/dpkg-deb, gpgv, GNU tar, POSIX shell,
AWK and standard Essential/coreutils tools. Each is checked by absolute path.
Missing tools are a blocker, never a reason to install an unverified bootstrap.

This candidate installs only native APT's transaction for the signed candidates
of `python3`, `python3-apt` and `ca-certificates`, without Recommends or Suggests.
Python package versions are **derived from authenticated fixed snapshot indexes**,
not invented here. Jammy's Python 3.10 is preparation-only: it must never select
the extension ABI, headers, build venv or runtime interpreter. The separate
reviewed CPython 3.12.13 chain remains mandatory downstream.

## CA-free HTTPS bootstrap without copying host trust

The active path is strict HTTPS. No `Verify-Peer=false`, HTTP fallback, redirect,
live mirror, custom PPA, `trusted=yes`, expired metadata waiver, or unauthenticated
package mode is available. The former HTTP-only draft is not a supported path or
fallback and must remain outside the publication candidate.

The future controller must materialize exactly the three public bytestrings in
`ca-inputs.lock.json` into `preparation/bootstrap-inputs/`. The small stdlib-only
`materialize_ca_inputs.py --output <fresh-real-directory>` implements this future
HTTPS fetch, exact size/SHA256 verification and redirect refusal. It must itself
be source-anchored and invoked only after the separate preparation/run approval.
It was parsed, not executed, during authoring. Its Python requirement is on the
trusted controller, not inside the minimal image. A known controller interpreter
must be selected; discovering/downloading another interpreter is not implicit.

The three inputs are:

1. Fixed `jammy-security.InRelease`, SHA256
   `eaa0a9cb0ad5ea72dcaf84e81a1f13c82a5afbb08700bee189b9b7e2c638c5a9`.
2. Its signed `main/binary-amd64/Packages.xz`, SHA256
   `193e21d53e0d7fbf97fed37314da94d75223d4575b769aa95891d8ea1592471a`.
3. The exact signed `ca-certificates_20260816ubuntu0.22.04.1_all.deb`, 139540
   bytes, SHA256 `29ca037f49f7a4b41d67ad72946c2e541f3ae52d8f6ab1495d2af09aa391144b`.

The Dockerfile copies those three exact filenames into `/opt/urp-bootstrap-seed/`.
It copies `bootstrap.sh` and all eight AWK helper files into
`/usr/local/lib/urp-bootstrap/`. The exact future invocation is
`/bin/sh /usr/local/lib/urp-bootstrap/bootstrap.sh --install-bootstrap`.
An enclosing compiler-preparation Dockerfile may provide these same fixed paths
and then run its independently reviewed `prepare_packages.py` closure gate.

That package version is in **jammy-security/main**, not jammy-updates/main. The
retained updates index instead contains `20260601~22.04.1`; the distinction was
checked directly against all retained signed metadata during authoring.

Inside the image, before the first network request:

- Copy and fingerprint the pinned image's Ubuntu archive keyring. No controller
  keyring or alternate signing key is supplied.
- Check fixed InRelease hash/size, `gpgv` signature with the exact reviewed signer
  `F6ECB3762474EDA9D21B7022871920D1991BC93C`, distribution identity and signed index
  hash/size, then the exact signed CA package record and `.deb` hash/size.
- Use `dpkg-deb --fsys-tarfile` to decode data only. Never install or execute a
  maintainer script at this stage.
- Inspect the tar listing, allowing only regular, unique, bounded, path-safe
  `./usr/share/ca-certificates/mozilla/*.crt` members. Each selected member is
  streamed with `tar --to-stdout` into one checked public CERTIFICATE PEM bundle.
  No archive path is extracted onto the filesystem, no private key is accepted,
  and no update-ca-certificates helper is executed.
- Point native APT's HTTPS `CaInfo` only at that public-data bundle. The host's CA
  directory, Python prefix, libraries, keyring, tools and environment are absent.

The seed's exact signed bytes are independently checked again in the complete
native APT metadata/expiry gate before any package installation. Public CA seeding
does not itself claim native APT's date/Valid-Until acceptance.

## Native APT transaction gates

`bootstrap.sh --install-bootstrap` is a future explicit container-install mode,
not a permission token or proof of container isolation. It re-execs with an empty
allowlisted environment. Do not invoke it on a host.

1. Check Ubuntu 22.04, native amd64, no foreign architecture, root preparation
   identity, initial dpkg audit, fresh output and regular helper/keyring inputs.
   Capture base status, OS identity, native tool hashes, keyring and helper bytes.
2. Fetch only the fixed `20261021T000000Z` three suites' reviewed InReleases over
   verified HTTPS; verify fixed hash/size and base-keyring signature before index
   parsing. Fetch main/universe amd64 `.xz` indexes only by signed SHA256/size.
   Native apt-helper supplies decompression without a separate xz executable.
3. Construct a fresh local signed metadata repository. Run native APT update with
   date and Valid-Until checks enabled and all insecure modes disabled. Base APT
   config/hooks, preferences, sourceparts, proxy environment and other keyrings
   cannot enter the isolated APT configuration. The fixed URL supplies snapshot
   selection directly; no unsupported `apt -S` feature or live mirror is assumed.
4. Select each root candidate with an empty installed-status view, then require
   an exact record in the authenticated indexes. A newer installed root does not
   silently override the selected snapshot candidate.
5. Solve against the actual base inventory with native APT. Reject removals,
   foreign/unknown architectures, held-package changes, reinstalls, downgrades,
   malformed or duplicate plan entries. Every planned package must have one
   unambiguous signed identity/hash/size. Re-solve with all transaction versions
   pinned and require the same plan.
6. Use native APT's printed local URIs only to obtain bounded cache filenames.
   Verify the complete filename/size mapping against signed transaction records.
   Download each archive directly from its signed fixed-snapshot Filename, then
   verify SHA256, size and dpkg-deb control identity before copying to APT's cache.
7. Require unchanged initial dpkg status, a matching final no-download simulation,
   then install exact pins using `--no-download --no-remove`. This future root
   preparation phase can execute only authenticated Ubuntu maintainer scripts,
   never project/F2C build hooks.
8. Require the complete installed inventory delta to equal the plan exactly,
   selected roots present, native APT check and dpkg audit clean, system Python
   3.10/python3-apt 2.4 usable, and a system CA file present. Preserve the result
   rather than cleaning archives or hiding failures.

Any date expiry, newer conflicting base version, unexpected APT grammar, HTTP/TLS
failure, missing seed, unsafe tar member, unavailable signed record, cache mismatch
or failed script is `BLOCKED`; the script performs no destructive repair or
snapshot substitution. None of those real-environment predicates has yet been
measured. A failed Docker build can retain its local layer only according to the
controller's private evidence collection, so the controller must collect failed
preparation logs without publishing raw logs or leaking credentials.

## Preserved evidence and required downstream closure

`/opt/urp-bootstrap-evidence/` retains original and final dpkg status, inventory
TSVs, copied source helpers and base tool/keyring hashes, CA seed archives/metadata
and public bundle, all three signed InReleases, six compressed/full indexes,
effective APT config, native root policies, simulations, transaction records,
URI/cache mappings, every downloaded `.deb`, controls, private logs, timestamps,
`bootstrap.json` and a complete `SHA256SUMS` for regular evidence files.

The TSV formats are tab-separated, without headers:

- `signed-packages.tsv`: name, version, architecture, Filename, Size, SHA256,
  suite, component. Duplicate identical records across suites are retained.
- `roots.tsv`: name, selected version.
- `transaction.tsv`: name, architecture, version, old-version (`-` for new),
  action (`install` or `upgrade`), Filename, Size, SHA256, suite, component.
- `base.tsv` / `post.tsv`: name, architecture, version, selection (`install` or
  `hold`). Full original dpkg status remains authoritative evidence as well.

The receipt deliberately says `full_closure_verified: false` and
`actual_base_identity_verified_here: false`. Native APT's authenticated bootstrap
transaction is **not** the later independent authenticated closure proof of every
already-installed relevant base dependency or actual package-owned byte.

The downstream python3-apt closure gate MUST include these three bootstrap roots,
all bootstrap transaction packages, all toolchain roots/transactions, and the
Essential base set. It must link before/after inventories and this evidence to the
observed base image and final prepared image, authenticate retained providers,
and preserve the existing no-downgrade/no-removal and owned-byte checks. Merely
running the old toolchain roots unchanged could omit bootstrap-only libraries;
that is not accepted. Neither this bootstrap receipt nor a successful import may
be treated as compiler/ABI/geometry/full-suite/runtime portability acceptance.

## Verification performed

Run the portable pure test suite with an authoring interpreter:

    python -B -m unittest discover -s preparation/bootstrap -p test_bootstrap.py -v

The suite invokes only shell syntax checking and AWK over synthetic fixture data;
it does not run the bootstrap/controller materializer or native provisioning.
In addition, authoring parsed all three retained InReleases and all six retained
Packages indexes, matching the reviewed stanza counts 6090/58923, 21642/5960 and
20377/5017. CA lock sizes/hashes and its selected package row were checked against
those retained signed bytes. This is syntax/data parsing evidence, not a new
signature verification, live network observation, or successful container run.
