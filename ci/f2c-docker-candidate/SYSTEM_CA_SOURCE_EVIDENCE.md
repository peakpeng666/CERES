# Jammy system CA-file source evidence

Research date: 2026-10-22. This is source and package-data evidence, not a hosted filesystem snapshot. The accompanying [machine-readable record](preparation/evidence/system-ca-package-source.json) preserves exact package-index stanzas, archive identities, source locations, public URLs and verification limits.

## Finding and remaining observation limit

The inspected Jammy packages use two different paths: OpenSSL's compiled default CA file is `/usr/lib/ssl/cert.pem`, while Ubuntu's certificate maintenance script generates `/etc/ssl/certs/ca-certificates.crt`. None of the inspected `openssl`, `libssl3` or `ca-certificates` data archives provides `cert.pem`, and their relevant installation scripts do not create that alias. Thus a successful standard bundle generation does not, by itself, satisfy the bootstrap's existing `ssl.get_default_verify_paths().cafile` predicate. This is a demonstrated package/default-path compatibility mismatch, not evidence that OpenSSL is an absent dependency.

The v10 public result is `BLOCKED_post_install_verification__ca_file_missing`. Its helper reached this predicate after the imports and Python/APT checks passed. That result does **not** tell us whether `/etc/ssl/certs/ca-certificates.crt` also exists, nor does it independently identify the loaded library bytes or default path. The mismatch must not be represented as proof that the generated bundle exists or that no second defect remains. Provisioning a fixed alias must still require the intended generated bundle and retain the subsequent verification gates; no fallback to an unrelated trust source is justified by this evidence.

## Independently reproducible causal chain

1. Exact Ubuntu source `debian/rules` line 33 configures `--openssldir=/usr/lib/ssl`. Lines 121–123 move and link only `certs`, `openssl.cnf` and `private`. `debian/openssl.install` omits `cert.pem`. `debian/openssl.postinst` only creates the `/usr/lib/ssl` directory alias if that location does not already exist; it never creates a `cert.pem` alias.
2. The matching `openssl` binary data archive confirms those links and the absence of `cert.pem`. The matching `libssl3` archive likewise has no `cert.pem` member.
3. Read-only disassembly of the archive's `usr/lib/x86_64-linux-gnu/libcrypto.so.3` proves `X509_get_default_cert_file@@OPENSSL_3.0.0` at virtual address `0x2294e0` returns the string at `0x35c5ca`. Read-only `.rodata` inspection identifies it as `/usr/lib/ssl/cert.pem`. The inspected member SHA256 is `7cc817ff1841b59516d684f357bc0c3fc2cc52c3fa9b4ffb8ce8bdc215f02915`.
4. [CPython 3.10.12 `_ssl.c`](https://raw.githubusercontent.com/python/cpython/v3.10.12/Modules/_ssl.c), lines 4906–4935, obtains the defaults from `X509_get_default_cert_file_env()` and `X509_get_default_cert_file()`. Exact retained `libpython3.10-minimal` member `usr/lib/python3.10/ssl.py`, lines 438–449, applies the environment override and returns a file path only when `os.path.isfile()` succeeds. The bootstrap starts with a clean environment, without an inherited `SSL_CERT_FILE` override.
5. Exact `ca-certificates` control member `postinst`, lines 141–150 and 153–167, runs `update-ca-certificates`; `triggers` registers its two update triggers. Data member `usr/sbin/update-ca-certificates`, lines 27–32, selects the conventional Ubuntu bundle. Lines 90–101 append selected certificates to its temporary file; lines 190–197 install that temporary file only if it exists. None of these operations creates `/usr/lib/ssl/cert.pem`.

[Python's API documentation](https://docs.python.org/3.10/library/ssl.html#ssl.get_default_verify_paths) documents the missing-file result. [OpenSSL 3.0 documentation](https://docs.openssl.org/3.0/man3/SSL_CTX_load_verify_locations/) distinguishes default file and directory locations; successful default-path configuration does not guarantee a default file exists. The [Ubuntu certificate-maintenance manual](https://manpages.ubuntu.com/manpages/jammy/man8/update-ca-certificates.8.html) identifies the generated bundle location.

## Archive and metadata identities

All binary package URLs below use the fixed [20261021T000000Z snapshot](https://snapshot.ubuntu.com/ubuntu/20261021T000000Z/). Required package records are from `dists/jammy-security/main/binary-amd64/Packages.xz`; their exact stanzas are preserved in the JSON.

- `openssl_3.0.2-0ubuntu1.30_amd64.deb`: SHA256 `a5b9484a287aba47096fa9ea9821c5c98cf95b8dd726786e7c3a3311fb8eb3b4`.
- `libssl3_3.0.2-0ubuntu1.30_amd64.deb`: SHA256 `d1b4636e32ac6d3b95d583ac05ac548b1aa248414bae1c51b14f54be0f8fd7bc`.
- `ca-certificates_20260816ubuntu0.22.04.1_all.deb`: SHA256 `29ca037f49f7a4b41d67ad72946c2e541f3ae52d8f6ab1495d2af09aa391144b`.
- `libpython3.10-minimal_3.10.12-1~22.04.18_amd64.deb`: SHA256 `ccbd08562769e0a38b34005dc8bde200838e6c6530cc04ac34525129ed42d847`.

The retained `jammy-security.InRelease` SHA256 `eaa0a9cb0ad5ea72dcaf84e81a1f13c82a5afbb08700bee189b9b7e2c638c5a9` matches the frozen bootstrap anchor. Its package-index entry matches the retained index SHA256 `193e21d53e0d7fbf97fed37314da94d75223d4575b769aa95891d8ea1592471a` and size 3,643,160 bytes. All four archive hashes and sizes match the index. This research did not independently repeat signature verification: `gpgv` was unavailable, and no installation was attempted. The frozen bootstrap's separate signature gate remains necessary.

The [official source `.dsc`](https://snapshot.ubuntu.com/ubuntu/20261021T000000Z/pool/main/o/openssl/openssl_3.0.2-0ubuntu1.30.dsc) is 2,486 bytes with SHA256 `22b1c851fb5c229b27f497b1867e994ab2ad2f90d288d2dc3bd6eef15f6307bc`. It names the [Debian source archive](https://snapshot.ubuntu.com/ubuntu/20261021T000000Z/pool/main/o/openssl/openssl_3.0.2-0ubuntu1.30.debian.tar.xz), 319,128 bytes, SHA256 `b2f4d75e1eba248397d8e4f4a029195ec3bb0e971b2e975594115a1c79b31c47`. That checksum was verified. No independent `.dsc` signature verification or signed Sources-index authentication is claimed.

To reproduce, verify the metadata/archive hashes, decode `.deb` control/data tar streams without installing them, inspect the named source members and archive links, and use a static ELF disassembler to inspect the indicated accessor and string. Do not import the downloaded libraries or execute maintainer scripts. All research here was data-only; no native build, Docker run, raw hosted-log retention or publication was performed.
