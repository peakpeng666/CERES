# Python prerequisite source evidence

Research date: 2026-10-22. Scope: read-only primary-source research and inspection of retained public Ubuntu metadata/package data. No package installation, package-code execution, Docker execution, native build, or publication was performed for this research.

## Attribution limit

The observed v9 public result is `BLOCKED_post_install_verification__python_prerequisites`. That composite stage contains interpreter startup, imports, and three predicates. It does not identify which operation failed. The sources below establish API semantics and expected package compatibility, not the state of the hosted process. No hosted root cause is established, and no private hosted log was retrieved or retained for this investigation.

The diagnostic correction should preserve the original requirements: Python 3.10, successful `apt_pkg` and `ssl` imports, `apt_pkg.VERSION.startswith("2.4.")`, and a truthy default `cafile`. Finite failure codes distinguish these operations without weakening the gate. This research does not justify removing isolated mode, accepting a missing CA file, or bypassing TLS validation.

## 1. APT and python-apt versions are different identities

The official Python APT documentation defines `apt_pkg.VERSION` as the version of APT, not python-apt. `apt_pkg.LIB_VERSION` is a separate library-version value. Therefore the Debian package version `python3-apt=2.4.0ubuntu4.1` is not itself the value tested by the bootstrap predicate.

- [Python APT VERSION documentation](https://apt-team.pages.debian.net/python-apt/library/apt_pkg.html#apt_pkg.VERSION)
- [Python APT LIB_VERSION documentation](https://apt-team.pages.debian.net/python-apt/library/apt_pkg.html#apt_pkg.LIB_VERSION)

The retained snapshot lists `apt` and `libapt-pkg6.0` at `2.4.5` in jammy and `2.4.14` in jammy-updates. Both versions satisfy the prefix predicate. The same snapshot lists `python3-apt=2.4.0ubuntu4.1` in jammy-updates and jammy-security, with a dependency on Python >=3.10 and <3.11. These metadata facts do not prove what library the failed process actually loaded.

## 2. Isolated mode preserves system package search paths

Python 3.10 `-I` implies `-E` and `-s`, not `-S`. It excludes the script/current directory and user site-packages, and ignores `PYTHON*` configuration variables. It does not generally disable system site initialization. `-B` disables writing bytecode caches.

- [Python 3.10 command-line options](https://docs.python.org/3.10/using/cmdline.html#cmdoption-I)
- [Python site initialization](https://docs.python.org/3.10/library/site.html)
- [Ubuntu Jammy python3-apt file list](https://packages.ubuntu.com/uk/jammy/amd64/python3-apt/filelist)

The Ubuntu file list places the extension at `/usr/lib/python3/dist-packages/apt_pkg.cpython-310-x86_64-linux-gnu.so`. That web listing is the release package, not independent evidence of the installed update package's bytes.

The exact retained `libpython3.10-minimal=3.10.12-1~22.04.18` archive provides stronger source evidence for path handling. Its `usr/lib/python3.10/site.py` lines 381–386 construct global dist-packages paths, including `prefix/lib/python3/dist-packages`; line 623 calls `addsitepackages()` unconditionally. The isolated flag at lines 627–628 only guards readline setup. These files were inspected as archive data, not imported or executed.

## 3. Default CA paths are not certificate-validation results

`ssl.get_default_verify_paths()` resolves OpenSSL's default file/directory paths and returns `None` for a nonexistent file/directory. Exact retained `usr/lib/python3.10/ssl.py` lines 441–449 obtain the defaults, apply environment overrides, and use `os.path.isfile()`/`os.path.isdir()`. The bootstrap's `cafile` predicate therefore checks resolved file existence, not certificate parsing, trust content, expiry, or a TLS handshake.

- [Python default verify-path API](https://docs.python.org/3.10/library/ssl.html#ssl.get_default_verify_paths)
- [CPython 3.10 ssl.py](https://raw.githubusercontent.com/python/cpython/3.10/Lib/ssl.py)
- [OpenSSL 3.0 default CA locations and loading APIs](https://docs.openssl.org/3.0/man3/SSL_CTX_load_verify_locations/)
- [Ubuntu update-ca-certificates manual](https://manpages.ubuntu.com/manpages/jammy/man8/update-ca-certificates.8.html)

OpenSSL documents separate default CAfile and CApath locations, overridden by `SSL_CERT_FILE` and `SSL_CERT_DIR`. `SSL_CTX_set_default_verify_paths()` can report success even when a default location is missing. Consequently that API's success would not substitute for the bootstrap's existing file-existence predicate. A directory-based trust configuration is also not evidence that this particular file predicate passed.

The bootstrap starts with `env -i` and configures APT's `Acquire::https::CaInfo` to use its separately assembled bootstrap bundle. Successful APT HTTPS retrieval therefore does not establish Python's default `cafile`. The retained ca-certificates postinst calls `update-ca-certificates` during configuration and queues its update trigger; this is source behavior, not a direct observation of the hosted filesystem.

## 4. Reproducible public evidence identities

Snapshot base: https://snapshot.ubuntu.com/ubuntu/20261021T000000Z/

Retained `InRelease` files match the frozen bootstrap anchors. Retained main/amd64 package-index sizes and SHA256 values match their corresponding `InRelease` entries. This research rechecked those hash relationships; it did not independently rerun signature verification.

- `dists/jammy/InRelease`: `c14060cd8c6d625874dfcb9523a35a395bf4865c28b6b6a82569ef326fe92dc6`
- `dists/jammy-updates/InRelease`: `34beb69f860de8eb98f2315b4c38c2542b1b005153652e399d898c892431adda`
- `dists/jammy-security/InRelease`: `eaa0a9cb0ad5ea72dcaf84e81a1f13c82a5afbb08700bee189b9b7e2c638c5a9`
- `dists/jammy/main/binary-amd64/Packages.xz`: `37cb57f1554cbfa71c5a29ee9ffee18a9a8c1782bb0568e0874b7ff4ce8f9c11`
- `dists/jammy-updates/main/binary-amd64/Packages.xz`: `0d1e4c9a8a5efe021366ad516bdb4c70da90752095f30f88c6716604f296174c`
- `dists/jammy-security/main/binary-amd64/Packages.xz`: `193e21d53e0d7fbf97fed37314da94d75223d4575b769aa95891d8ea1592471a`

Public package paths below are relative to the snapshot base. Locally retained archives were hashed and compared with those metadata records:

- `pool/main/p/python3.10/libpython3.10-minimal_3.10.12-1~22.04.18_amd64.deb`: `ccbd08562769e0a38b34005dc8bde200838e6c6530cc04ac34525129ed42d847`. Inspected members: `usr/lib/python3.10/site.py` and `usr/lib/python3.10/ssl.py`.
- `pool/main/p/python3.10/libpython3.10-stdlib_3.10.12-1~22.04.18_amd64.deb`: `ee09afbc981a4aaa00053f019eab6b45dcc4dbeb4ca4fcc4277eff539e09f228`. Hash verified; neither cited Python source file is in this archive.
- `pool/main/c/ca-certificates/ca-certificates_20260816ubuntu0.22.04.1_all.deb`: `29ca037f49f7a4b41d67ad72946c2e541f3ae52d8f6ab1495d2af09aa391144b`. Inspected control member: `postinst`, especially lines 141–150.

Metadata-only identities, not locally executed/imported package observations:

- `pool/main/p/python-apt/python3-apt_2.4.0ubuntu4.1_amd64.deb`: `bfe0dd80531613cfeff111d7b4f60255b4819149c906b21d93deb501e4e19230`.
- `pool/main/a/apt/libapt-pkg6.0_2.4.14_amd64.deb`: `0cc17a8dce1d3a9247a17f653476637f0898cea897875b726157418b16fe6b6e`.
- `pool/main/a/apt/apt_2.4.14_amd64.deb`: `0d6b292142f54532817c593333480f69ec2b5449df09f45a3a45fa8be19734b8`.
