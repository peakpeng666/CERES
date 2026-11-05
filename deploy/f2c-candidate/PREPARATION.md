# Ubuntu 22.04 Fields2Cover candidate preparation

Status: v7 repairs a reproduced hermetic test dependency, not a package or native acceptance failure. Hosted v6 stopped in helper tests before APT; the canonical target identity is still unknown. All reporting logic now lives in an indexed module shared by the workflow and tests. Publication requires exact workflow/bootstrap parity and all helper/interface tests in both the source tree and a fresh source-index-only runtime under a clean environment. V6 canonical metadata verification and all scientific, transaction, provenance and offline-isolation gates are unchanged. No native Jammy success or production promotion is claimed.

## Verified upstream inputs

The official Google OR-Tools 9.15.6755 Ubuntu 22.04 C++ archive was downloaded as data from its official release URL. It is 55,938,522 bytes and SHA256 `0b30114d7c05f0596286bf3ef8d02adcf5f45be3b39273490e6bb74a2a9bd1ea`, matching release metadata. All 10,755 headers and CMake interface files are byte-identical to the previously reviewed official Debian 12 archive. `compare_interfaces.py` reproduces this comparison without extracting or executing binaries; six standard-library negative/positive tests passed. Equal headers do not prove runtime C++ ABI compatibility.

The C++ archive supplies headers and compile-interface references ONLY. Do not use its CMake package as-is, because it targets its own native libraries. The preparer constructs a reviewed shim binding the exact Python OR-Tools wheel provider instead. `ortools-wheel-members.json` hashes 108 native members of that exact wheel without loading them. It is not an ELF closure or a runtime acceptance result.

`official-source-inputs.json` records official source/runtime URLs, versions, commits, bytes and SHA256s. `build-requirements.lock` records immutable build-tool wheel hashes. These references alone are not a complete reviewed-input lock.

## Authenticated Ubuntu metadata

Canonical snapshot `20261021T000000Z` is authenticated: independently trusted Debian keyring → signed Debian Release/Packages → hash-verified public Ubuntu keyring package → three Ubuntu Release signatures → six amd64 main/universe indexes → 13 exact root package records. The offline metadata verifier was independently rerun successfully. This does not claim root payload verification or a complete dependency transaction. See PACKAGE_METADATA_NOTES.md.

## Host contract and promotion gates

Target the declared Ubuntu 22.04 amd64 host, GCC 11 and exact application CPython 3.12.13. GDAL/GEOS/TinyXML2/TBB and their transitive system libraries come from authenticated Ubuntu packages. Do not bundle Debian packages, replace SONAMEs with symlinks, or add broad LD_LIBRARY_PATH.

Derive install RUNPATH from actual installed wheel locations, and repeat this inspection for the newly built wheel. A typical verified layout has `fields2cover/_fields2cover_python.so` and provider directories `ortools/.libs` and `ortools.libs`; only after verification do its relative paths become `$ORIGIN/../ortools/.libs` and `$ORIGIN/../ortools.libs`.

Keep application source/tests at 18557cfc61f291e8ac5fa4a80d9df212e1df0030 unchanged. Preserve the historical accepted manifest. A separate generated candidate identity must record all new source, build, host, provider and output hashes; it must not claim the historical extension identity.

Required before promotion: static Jammy ELF/provider closure, unchanged mixed-native smoke, all three native geometry contract cases with no skips, full Lab suite with official maps and verified dataset, relocated fresh venv with no LD_LIBRARY_PATH/LD_PRELOAD and actual loaded-provider map, both import orders, and independent numerical review. Two clean build comparisons are required before claiming bit reproducibility.

## Implemented same-job path

The workflow template selects an authorized fresh standard Ubuntu 22.04 runner, verifies the externally reviewed source-index digest, runs helper fixture tests, resolves and installs the authenticated bounded APT transaction, materializes locked application dependencies and official validation data, measures the actual live host/Python/provider inputs, builds in a mandatory isolated network namespace, and validates a relocated venv against unchanged source185 tests. See `recipe/README.md`, `recipe/PACKAGE_PREPARATION.md`, and `recipe/VALIDATION.md` for contracts and CLI details. Raw outputs stay private and ephemeral; public status is bounded to stage, approved identity and measured passing counts.

No runner-specific lock is invented or reused from Debian. Installed package closure, loader compatibility, native test success and resulting wheel hashes remain runtime measurements, not claims made by this template. A measured first build is not evidence of bit reproducibility.

## Publication boundary

Only reviewed portable source/helpers and sanitized reference metadata are prospective public files. Do not publish this whole directory: it contains downloaded archives and local evidence. No raw native archives, experiment output, environment dumps, runtime artifacts, credentials or private assets belong in a public change.

## Official sources

- https://github.com/google/or-tools/releases/tag/v9.15
- https://github.com/google/or-tools/releases/download/v9.15/or-tools_amd64_ubuntu-22.04_cpp_v9.15.6755.tar.gz
- https://packages.ubuntu.com/jammy/libgdal-dev
- https://packages.ubuntu.com/jammy/libtinyxml2-dev
- https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2204-Readme.md
