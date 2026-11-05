# Existing-tool authentication for the capability-only probe

This preparation does not install packages, change subordinate-ID allocations,
create a root filesystem, invoke namespace helpers, or run package scripts.
It authenticates existing `/usr/bin/python3.10`, `unshare`, `newuidmap`, and
`newgidmap`, together with their relevant runtime inputs. An unavailable or
unmatched exact installed identity is a terminal blocker, not permission to
upgrade, downgrade, remove, or substitute anything.

## Source and bootstrap boundary

`prepare_capability_inputs.py` and its new `keyring_input.py` helper are stdlib-only. The metadata dependency
`jammy_metadata.py` remains copied unchanged from the reviewed Jammy package preparer:

`3ef3c611be036f06331e4c2f0567fa75db27072270b075c72dc1231a4644f558`

The externally anchored source index covers these modules, public-keyring data/provenance, and the probe. Metadata
code executes the exact bytes whose digest was checked, not a loader's second
read. Only its metadata/download/parsing routines are used; no APT solver or
transaction entry point is invoked.

The first system Python invocation and the read-only `gpgv`, `dpkg-query`, and
`dpkg-deb` operations are explicit bootstrap trust. Subsequent archive comparison
cannot retroactively authenticate that initial execution. The preparation later
authenticates those existing executables and relevant shared-library bytes too.
`dpkg-deb --field` and `--fsys-tarfile` inspect DATA; maintainer scripts and archive
executables are never run. The command grammar permits only these read-only
bootstrap modes, never helper, shell, package-install, or extraction commands.

## Separate immutable public-data trust anchor

The private-public-keyring-v1 protocol uses the exact indexed Canonical public-keyring bytes and independently documented full direct-signer fingerprint. It does not bootstrap its key from the same signed package chain that needs that key. The source data and provenance are externally hash-anchored before adoption; the fixed private copy is caller-owned, mode 0600, under a fresh mode-0700 directory, with no aliases or extra links. Its manifest is separate from host/runtime installed-file attribution.

A fully sealed memory file holds the already verified bytes. WRITE, GROW, SHRINK and SEAL protections, identity, exact size/hash and position are checked; only its internally generated `/proc/self/fd/N` and explicit inherited descriptor are accepted by the fixed verifier command. Mutable pathname rechecks alone are not the consumption guarantee. The descriptor closes before subsequent helpers. No default, additional or host keyring is accepted, and verifier success still requires the exact full direct signing fingerprint in machine status. Public-key packet inspection is not a substitute for the actual InRelease signature check.

Only the unused host public-keyring requirement and its installed ubuntu-keyring attribution are removed. All other root-owned/non-writable host-file and archive matching predicates remain. The private data is not a host installed-package file and receives no general root-ownership exemption. Initial Python/gpgv/dpkg bootstrap trust remains as above; later archive authentication is not retroactive.

## Official package evidence

The immutable Ubuntu snapshot is `20261021T000000Z`. The copied metadata module
pins the three InRelease hashes, sizes, Ubuntu archive signer, and official
snapshot origin. InRelease signature and exact hash checks precede use of each
signed index hash/size. Exact installed name, version, and architecture must be
members of those authenticated `jammy`, `jammy-updates`, or `jammy-security`
`main`/`universe` indexes. Archive hash/size and control identity are checked
before file bytes, modes, owners, aliases, and runtime dependencies are accepted.
No PPA, PGDG, arbitrary installed-version, or unsigned-owner fallback is allowed.

Official retained archive DATA confirms these Python layouts:

- `libpython3.10-minimal` and `libpython3.10-stdlib`, version
  `3.10.12-1~22.04.18`, provide the core sources/extensions and authenticated
  `sitecustomize.py` and `_sysconfigdata` aliases.
- `libpython3.10-dev` at the same version provides
  `config-3.10-x86_64-linux-gnu/python-config.py`, so limiting ownership to only
  the first two packages would reject a normal development installation.
- `python3-gdbm` and `python3-tk`, version `3.10.8-1~22.04`, provide additional
  extensions and `tkinter` sources under the same Python library tree.

These observations are archive-layout evidence, not claims about installed
versions or helper availability on a future runner. The fixed optional owner
map permits `libpython3.10-dev`, `python3.10-dev`, `libpython3.10-testsuite`, and
`python3.10-examples` only with signed source `python3.10`; `python3-gdbm` and
`python3-tk` require signed source `python3-stdlib-extensions`. Every file is still
matched to the exact installed package's authenticated archive. Other importable
files are not silently skipped.

## Runtime and lock contract

Run system Python with `-I -B -S -X pycache_prefix=$CANDIDATE_ROOT/unused-pycache`.
That prefix and `/usr/lib/python310.zip` must remain absent. The only supported
Python search path is the standard zip path followed by `/usr/lib/python3.10`
and its `lib-dynload` directory. All `.py` and `.so` inputs are enumerated;
symlinked package directories and sourceless `.pyc` inputs outside the ignored
`__pycache__` directories are rejected. The fresh prefix prevents stale cache
reads; `-B` prevents cache creation and `-S` excludes site hooks.

A data-only x86-64 ELF reader rejects nonstandard interpreter, RPATH/RUNPATH,
audit, filter, and auxiliary-library directives. All relevant loader-cache and
default/hwcaps library candidates are authenticated, not merely the first one.
Unsupported formats or foreign runtime providers fail closed. This is a narrow
tool/runtime closure, not the runner's whole scientific package closure.

`capability-inputs.lock.json` uses schema 3 and protocol `private-public-keyring-v1`, with a separate `trust_anchor` data manifest. It binds source bytes, boot ID, caller IDs/groups,
host configuration, optional subordinate-ID-file absence, metadata/archive
evidence, tools, Python sources, and runtime files. It is exclusive and mode
0600. `verify_lock(root, expected_lock_sha256)` rechecks the exact lock digest
and measured state using no native command or network access. A held FD to the
original authenticated Python inode is accepted; a copied interpreter is not.

The probe separately determines whether existing subordinate allocations and
kernel capabilities suffice. Authentic existing root-owned setuid helpers are
allowed; no new setuid installation, permission change, or allocation is made.

## Fixed NSS callable-provider boundary

The only supported passwd/group policies are exactly `files` or `files systemd`,
without action overrides. `subid` must be absent or exactly `files`. The probe
also requires unique literal local passwd owner names for subordinate ranges.
Local-name success alone is not a complete callable-provider proof: a files
backend error could reach the configured fallback. Therefore any allowed
systemd fallback is authenticated before helper execution, including its ELF
dependency closure, rather than ignored on the assumption that files succeeds.

Retained signed archive DATA for `libnss-systemd` version `249.11-0ubuntu3.22`
(archive SHA256 `0169663ff740119ba840028e6d6345b0da9e3e21950231f2a28aec3810ea78fe`)
places the module at `/usr/lib/x86_64-linux-gnu/libnss_systemd.so.2`. Its only
DT_NEEDED entries are `libc.so.6` and `ld-linux-x86-64.so.2`; it has no
RPATH/RUNPATH. The existing strict ELF rules are unchanged. An exact installed
package identity absent from the signed snapshot remains terminal.

The signed Jammy [glibc 2.35 source](https://snapshot.ubuntu.com/ubuntu/20261021T000000Z/pool/main/g/glibc/glibc_2.35.orig.tar.xz)
dispatches `files` to built-in libc functions before generic NSS dlopen.
Ubuntu's [2.35-0ubuntu3.15 patch set](https://snapshot.ubuntu.com/ubuntu/20261021T000000Z/pool/main/g/glibc/glibc_2.35-0ubuntu3.15.debian.tar.xz)
retains that ordering and adds the fixed `libnss_systemd-2.35.so` retry if the
primary soname fails. Both sonames' loader candidates are covered: existing
files require the exact authenticated `libnss-systemd` owner; absent candidates
are frozen and rederived. No arbitrary NSS module or loader-path exemption is
accepted. The private `nss` record contains `providers`, `fallback_paths`, and
`absent_paths`, all rechecked by `verify_lock`.

The raw `/proc/self/loginuid` value is frozen without whitespace normalization.
Only canonical decimal uint32 values are parsed; the probe further requires
the unset sentinel or a unique local passwd UID. In the exact Linux glibc
getlogin path, the unset sentinel returns positive ENXIO before NSS or tty
fallback. Missing or malformed input is not treated as unset. The fixed
`/var/run/nscd/socket` must remain absent, because passwd lookup may consult
nscd before the configured NSS sequence. The probe repeats these state checks
around helpers. Existing nsswitch, passwd, group, and subordinate-ID files
remain frozen; no host configuration or daemon is changed.

This authenticates the fixed callable module and ELF bytes, not external
systemd userdb daemon state, all possible account data, or the entire host.
Bootstrap trust remains the boundary described above.

Only the closed schema-1 PASS/FAIL preparation code and, for mode failures, fixed subject/check labels are public. The latter never contain measured paths, owners, UID/mode values or exception text. Tool-specific mode codes must match their fixed tool subject, except the legacy UNKNOWN/UNKNOWN pair. Other preparation receipts retain exactly their original three fields. Host inventories,
installed versions not authenticated to the snapshot, arbitrary exception text,
and subprocess stderr are not projected. Pure-data and mocked fixtures are not
a claim that a namespace or root filesystem has been executed successfully.

## V5 package-hop attribution

The lock is schema 3 with the unchanged public-keyring protocol and separate `member_protocol: signed-package-hop-attribution-v1`. Its `member_attributions` mapping binds each authenticated path's ordered non-usrmerge symlink hops and terminal regular member to unique exact signed package/archive records. A different package may own a canonical terminal target, but its identity and archive must be authenticated independently; no archive union or filename-based ownership inference is accepted. Every literal alias target and terminal byte/size/mode/UID/GID predicate remains enforced.

The four pre-existing root usrmerge aliases retain only their exact source-reviewed layout rule and are explicitly distinguished from signed package-owned members. Reuse replays complete used-member projections bound to exact authenticated package/archive identities, while rechecking the measured source, dpkg status, host files, archive bytes and snapshot inputs. This relies on the reviewed preparation code and externally measured lock hash; it does not claim independent archive reparsing or native owner-query execution on each reuse. Old schema-2 locks cannot be reused by the v5 namespace launcher. The observed v4 generic byte-rejection and the separately reproduced canonical Python-package alias defect remain distinct evidence.

## V6 exact optional Source mappings

Exactly three additional optional owner names are supported: python3-distutils and python3-lib2to3 require Source python3-stdlib-extensions; python3.10-venv requires Source python3.10. The existing fixed-snapshot installed-version selection, package/archive attribution and per-hop member checks still apply. No Source-family wildcard is permitted, and no package is installed or required by this addition. The schema-3 lock and both existing protocols are unchanged. The indexed canonical inventory is a review/test fixture, not a runtime authentication bypass.
