# Compiler v14: Debian control header repair

## Demonstrated cause and scope

Compiler13 run39869016825 / job120138424612 reported one `post-delta-version` mismatch. Its signed-index coordinate resolves to fonts-dejavu-core, 2.37-2build1, all, Jammy/main stanza433 at snapshot20261021T000000Z. The hosted raw observed version was not retained; no literal hosted version is claimed.

The old parser recognized only `: ` headers. Dpkg emits `Conffiles:` followed by indented records. Ignoring that header left the preceding field active. This package has no dependency fields between Version and Conffiles, so all12 conffile records became part of the parsed Version. The authenticated-archive-derived status fixture reproduces exactly the original validator's version rejection without installation. It is a reconstructed fixture, not a hosted status dump.

## Exact public data

- Archive: https://snapshot.ubuntu.com/ubuntu/20261021T000000Z/pool/main/f/fonts-dejavu/fonts-dejavu-core_2.37-2build1_all.deb
- Size1041300; SHA256 c33fe8bf33b50d99d6448804a090123b1bbba37d7a8703c630695676d110a47d. Matches retained authenticated package-index stanza.
- Control archive contains control, conffiles, md5sums only. No maintainer scripts or triggers. conffiles lists12 paths. The fixture's conffile MD5 values are calculated from exact archive payloads, not invented.
- Ubuntu dpkg source: https://snapshot.ubuntu.com/ubuntu/20261021T000000Z/pool/main/d/dpkg/dpkg_1.21.1ubuntu2.6.tar.xz
- Size5017128; SHA256 f66de0b3430a5545699361168824976acfa85f1308f00dde5bc35f39e15e2d02. Matches retained source-index stanza. Internal archive directory is dpkg-1.21.1ubuntu2.4, preserved as distributed; not asserted to be the package version.
- lib/dpkg/parse.c68–80 defines Version, optional Config-Version/dependencies, then Conffiles. dump.c472–474 emits fields in table order; dump.c385 emits literal `Conffiles:\n`; lines386–392 append indented path/hash records. Config-Version is omitted for installed packages (dump.c88–95). Font package has no dependency fields.
- Debian Policy5.1: https://www.debian.org/doc/debian-policy/ch-controlfields.html specifies colon-delimited headers, optional horizontal whitespace, and multiline fields whose first line may be empty. Leading-space/tab continuation takes precedence over text resembling a header.

Read-only data extraction only. No archive scripts, dpkg installation, APT, Docker, or native build were executed. The dpkg cgit URL returned an anti-bot page; it was not accepted as source or bypassed. Evidence instead uses the independently hash-matched official Ubuntu source archive above.

## Repair and unchanged controls

Recognize colon-delimited headers, trim only horizontal space/tab around the initial value, preserve continuation priority and contents. Reject orphan continuations, malformed names/headers and duplicate exact headers. No package-specific exception. Original exact inventory validator, package selection, signed-index references and version comparison remain unchanged. Malformed/empty-header handling is parser correctness, not acceptance of version differences.

## Separately approved cleanup delta

The combined candidate also includes only the previously reviewed engine_controller.py and tests/test_controller_cleanup.py. Cleanup patch SHA2568a57fce23ae7e0ae895aaf350e5e7de7242fc98e5f980e8495a3942869bda77d; controller SHA25613cdabf3c7a65e0ee61534fd76ddaa41cc217d4f36e7858e697288353ead8650. This is independent of the parser repair and does not change build acceptance.
