# Trusted compiler preparation

This candidate prepares a compiler image and immutable `/inputs` for a separately
controlled offline build. It does not claim a native build, numerical acceptance,
fresh-runtime portability, or bit-reproducibility. `runtime/` is a separate unfinished
follow-on and is excluded from the compiler-only publication cohort.

## Trust and operation

The workflow must authenticate the externally reviewed source-index and copy held,
verified helper bytes before executing any helper. `prepare_image.py` additionally
checks that anchor and every source member. The clean application checkout must be
exactly `3acf840d1a0c16ab1cfcca15744e4ea6609ddb35`; only tracked `src/lab/pyproject.toml` and `src/lab/uv.lock` are selected (128 MiB total cap); each exported Git blob is checked,
and both selected inputs must be regular Git files, never symlinks.
No `.git`, untracked data, literature, experiments or results are exported.
`application-source.json` records the root tree and exact selected blob/hash/link
manifest; it explicitly does not claim a whole-repository materialization.

The local Docker Engine is invoked through its explicit Unix socket and a fresh
empty configuration. The sole base is the pinned official Ubuntu 22.04 amd64
manifest `98510ac07bb4545e389be502a8918b385c018bfe4ceeb5ac7e0375c782268b04`.
The trusted Dockerfile, not a `/.dockerenv` file, establishes placement of privileged
package preparation inside the image. No privileged container or host package,
permission, daemon, credential, network-security or namespace-policy change is used.

`bootstrap/` authenticates a public CA-data seed through the base Ubuntu keyring,
pinned signed Release and package index, before strict HTTPS APT bootstrap.
`prepare_packages.py` then independently authenticates the complete native APT
Depends/Pre-Depends, transaction and Essential closure against snapshot
`20261021T000000Z`, retaining no-removal/no-downgrade and exact archive/member gates.
`verify_bootstrap_closure.py` requires every bootstrap root/transaction package to
belong to that closure and preserves exact provider archive identity. Bootstrap
success alone is not full-closure acceptance.

UV 0.12.23 is a declared measured bootstrap input from its immutable setup action,
not an Ubuntu-signed package. Node/npm/frontend assets are absent from this cohort. The UV checksum
endpoint returned HTTP 403 during research and was not retried or bypassed. No
unavailable official checksum is invented. The bootstrap lock records selected
binary bytes. UV's exact baseline CPython 3.12.13 selector
uses the independently recorded official distribution metadata row and SHA256 in
`evidence/python-distribution-reference.json`. System Python 3.10 is bootstrap only,
never the F2C extension ABI. Only the selected UV binary is copied, no host libraries or unrelated tools.

## Future CLI sequence

After source authentication, run `bootstrap_tools.py collect --help` and
`prepare_image.py --help` for the exact bounded interfaces. The latter additionally
requires `--execute-trusted-preparation`, a fresh output directory and external
source/bootstrap digests. It will pull the pinned base and execute trusted online
preparation only when separately authorized. These actions were not run locally.

The prepared image uses the caller's nonzero numeric UID/GID and final `/inputs`
path. Its input index is frozen in-image, then exported from a stopped container
without starting it. `prepared-image.json` returns exact prepared/base image IDs,
application/source/bootstrap/artifact/input hashes and scope flags. The controller
must retain the exact image ID and exported input-index digest; never refreeze a
changed export. The offline phase is a separate engine-enforced network-none,
non-root, read-only-input, bounded-tmpfs protocol, not the historical unshare guard.

## Validation status

Only local source inspection and pure fixtures were run. No Docker image pull,
build, container run, package installation, native import, namespace proof or public
publication occurred. Fresh minimal-runtime preparation and current-app full-suite
acceptance remain separate pending stages. Compilation alone cannot establish them.
