# Jammy package metadata audit

Status: metadata authenticated; native Jammy dependency transaction and build not run.

## What is established

The fixed official Ubuntu snapshot is `20261021T000000Z`, architecture `amd64`, suites `jammy`, `jammy-updates`, and `jammy-security`, components `main` and `universe`. All three InRelease signatures were verified, and all six compressed package-index SHA256 values and byte counts match their authenticated Release payloads. Thirteen requested root package versions and their dependency fields, archive filenames, sizes, and SHA256 values were extracted from those indexes. They are real signed metadata, not guessed version pins.

The full evidence is under `metadata/`. `metadata-status.json` gives exact release hashes and dates. `snapshot-20261021T000000Z/root-package-pins.txt` is deliberately only a ROOT package list, not a complete install lock. `root_package_records.json` preserves source stanzas; `root_package_candidates.json` explains version selection. Package payloads for the thirteen roots were not downloaded or verified.

## Independent trust bootstrap

This Debian 13 host had no Ubuntu archive keyring and no `gpgv` executable. Initial isolated GPG checks therefore failed with NO_PUBKEY; their logs are retained as historical evidence, not final status.

The host did have an independently pre-existing Debian archive keyring and `sqv`. That keyring verified the already available Debian Trixie InRelease. Its SHA256 claim matched the full 56,620,099-byte Debian Packages index. That authenticated index identifies Debian's `ubuntu-keyring` version `2023.11.28.1-0.2`, filename `pool/main/u/ubuntu-keyring/ubuntu-keyring_2023.11.28.1-0.2_all.deb`, size 14,132 bytes, SHA256 `b2e7ed48431bf9e7978ae810bbdaf253093570d6e3f864e575b7c13554b81174`.

The .deb was downloaded as DATA from Debian, checked against that authenticated size/hash, and only the public `ubuntu-archive-keyring.gpg` was extracted. No package scripts were run and nothing was installed. This is not circular trust in an unverified Ubuntu package: the bootstrap comes from pre-existing Debian trust.

That extracted keyring verified all three Ubuntu InRelease files with signer `F6ECB3762474EDA9D21B7022871920D1991BC93C`. Every index hash was then checked AGAIN against the verifier's authenticated payload, rather than trusting the pre-verification parse. Full Debian index bytes are retained, locally xz-compressed; verify the decompressed hash against Debian Release. A copied Debian keyring is included only as evidence. A reviewer must use their independently trusted keyring, not treat an arbitrary bundled key as a new trust root.

Recheck offline using the metadata-only verifier:

`python3 metadata/verify_metadata.py --debian-trust-keyring /usr/share/keyrings/debian-archive-keyring.pgp`

It verifies the chain, extracts only public key data into a temporary directory, and checks all thirteen recorded root versions against authenticated indexes. It neither uses the network nor installs or executes package payloads. `metadata/verification-result.txt` contains the completed result.

## Exact root package candidates

- gcc-11 and g++-11: `11.4.0-1ubuntu1~22.04.3`
- binutils: `2.38-4ubuntu2.12`
- pkg-config: `0.29.2-1ubuntu3`
- libgdal-dev: `3.4.1+dfsg-1build4`
- libgeos-dev: `3.10.2-1`
- libtinyxml2-dev: `9.0.0+dfsg-3`
- libeigen3-dev: `3.4.0-2ubuntu2`
- libboost-dev: `1.74.0.3ubuntu7`
- libtbb-dev: `2021.5.0-7ubuntu2`
- zlib1g-dev: `1:1.2.11.dfsg-2ubuntu9.2`
- libbz2-dev: `1.0.8-5ubuntu0.1`
- ca-certificates: `20260816ubuntu0.22.04.1`

Selection is the highest Debian version present across the three specified suites, not a substitute for the target machine's APT resolver. `all` packages in amd64 indexes are included correctly. No backports, proposed, PPAs, ROS repositories, restricted, multiverse, or ESM sources were used.

## Credible closure procedure for a future native Jammy runner

1. Use the standard public Ubuntu 22.04 amd64 runner and record its image version plus complete PRE-install dpkg inventory, apt version, architecture/foreign architectures, package holds, and effective APT configuration. The hosted runner label is moving, not an immutable digest; do not claim bit-reproducibility from that label. A package closure is meaningful relative to this captured base, not an undocumented developer machine. If an immutable container is later separately reviewed, pin its verified digest; this audit does not introduce or launch a container.
2. Use a previously trusted Ubuntu keyring in that runner. Fix all three official Ubuntu sources to snapshot `20261021T000000Z` and only `main universe`. Jammy's native snapshot option requires APT 2.4.11 or later. Canonical documents `[snapshot=20261021T000000Z]`; apply it consistently to archive and security sources. Do not silently fall back to a live mirror, disable authentication, set `trusted=yes`, or mix in Debian packages. A reviewed fixed snapshot URL may also serve metadata, but must retain normal Release verification.
3. Run native APT update, retain all authenticated InRelease/index data, and compare to these audited hashes. Do not trust the snapshot name alone. Fix recommends/suggests and phased-update policy and record them. APT default/pinning policy and preinstalled versions can change its candidate selection; record actual `apt-cache policy` for every requested root.
4. Ask the NATIVE Jammy APT solver for a simulation of installing the exact root package versions, with `--no-install-recommends`. Preserve the full transaction. It must resolve versioned Depends, Pre-Depends, alternatives, virtual Provides, architecture qualifiers, existing base packages, and conflicts. Do not replace this with a hand-expanded `apt-cache depends` tree. If a held or newer base package prevents the fixed snapshot transaction, fail explicitly rather than quietly changing snapshots or allowing unreviewed downgrades.
5. On that authorized runner, download the exact solved transaction without installing first. Produce a transaction lock for every .deb: package, version, architecture, source suite/component, signed-index filename, size, SHA256. Independently compare each downloaded payload's bytes to its authenticated Packages record. Missing versions or mismatched hashes are hard failures. Preserve the cached .deb artifacts if offline rebuilding is required.
6. Install only that resolved, verified transaction into the pinned disposable Jammy base. Record the complete POST-install dpkg inventory and compare it to the transaction plus base. Preserve package-manager logs. Build using explicit `CC=gcc-11` and `CXX=g++-11`; record compiler versions, linked libraries, pkg-config/CMake resolution, and requested native tests.
7. Required provenance is the runner image version and inventories + authenticated snapshot metadata + verified transaction lock + complete post-install manifest + build/test evidence. This supports an auditable rebuild, not by itself bit-reproducibility on a moving hosted image. An independently pinned immutable base and two clean equivalent build outputs would be needed for a stronger claim. The 13 root pins alone are NOT this claim. Build tools supplied separately by the runner (for example CMake, Python/SWIG, Ninja/Make) must also be versioned in the final build provenance.

This metadata audit did not resolve a native Jammy install transaction, install any package, execute Ubuntu binaries, run F2C, start Codex, write GitHub, or edit ROS/application code. These remain separate stages, not hidden successes.

## Official references

- Ubuntu snapshot format, fixed snapshot sources, Jammy APT support: https://snapshot.ubuntu.com/
- Ubuntu archive authentication chain: https://manpages.ubuntu.com/manpages/resolute/man8/apt-secure.8.html
- Ubuntu keyring package role: https://packages.ubuntu.com/jammy/ubuntu-keyring
- Audited snapshot root: https://snapshot.ubuntu.com/ubuntu/20261021T000000Z/
- Bootstrap package: https://deb.debian.org/debian/pool/main/u/ubuntu-keyring/ubuntu-keyring_2023.11.28.1-0.2_all.deb

Research and verification performed on 2026-10-22 UTC. Exact snapshot Release dates are recorded in metadata rather than inferred from HTTP timestamps.
