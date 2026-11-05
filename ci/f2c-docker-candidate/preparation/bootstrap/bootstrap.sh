#!/bin/sh
# Trusted future container provisioning only. Never run on a host.
# No Python, curl, wget, gpg, xz executable, or downloaded helper is needed.
set -eu
if [ "$#" -eq 1 ] && [ "$1" = --install-bootstrap ]; then
    exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C LANG=C \
        HOME=/nonexistent DEBIAN_FRONTEND=noninteractive \
        /bin/sh "$0" --internal-clean-bootstrap
fi
[ "$#" -eq 1 ] && [ "$1" = --internal-clean-bootstrap ] || {
    printf '%s\n' 'SAFE_BOOTSTRAP_DIAGNOSTIC:EXPLICIT_CONTAINER_INSTALL_MODE_REQUIRED' >&2; exit 64;
}
umask 022
OUT=/opt/urp-bootstrap-evidence
HELPERS=/usr/local/lib/urp-bootstrap
SEED=/opt/urp-bootstrap-seed
ORIGIN=https://snapshot.ubuntu.com/ubuntu/20261021T000000Z
SIGNER=F6ECB3762474EDA9D21B7022871920D1991BC93C
phase=prerequisites
substep=entry
completed=false
started=false
failure() {
    result=$?
    if [ "$completed" != true ]; then
        if [ "$started" = true ]; then
            printf '{"schema":1,"status":"BLOCKED","phase":"%s","substep":"%s","full_closure_verified":false}\n' "$phase" "$substep" > "$OUT/failure.json"
        fi
        printf 'SAFE_BOOTSTRAP_DIAGNOSTIC:BLOCKED_%s__%s\n' "$phase" "$substep" >&2
        [ "$result" -ne 0 ] || result=1
    fi
    exit "$result"
}
trap failure EXIT
fail() { exit 1; }
run() {
    label=$1; shift
    "$@" > "$OUT/logs/$label.log" 2>&1 || fail
}
hash_check() {
    got=$(/usr/bin/sha256sum "$1"); got=${got%% *}
    [ "$got" = "$2" ] || fail
}
fetch() {
    # Certificate data is seeded from a separate hash-pinned, signed Ubuntu
    # package before the first network request. No host CA trust is copied.
    # Both HTTPS validation and the signed Release/SHA256 chain are mandatory.
    label=$1; url=$2; destination=$3; digest=$4; size=$5
    run "$label" /usr/lib/apt/apt-helper download-file "$url" "$destination" "SHA256:$digest"
    hash_check "$destination" "$digest"
    got_size=$(/usr/bin/stat -c %s "$destination")
    [ "$got_size" = "$size" ] || fail
}
lookup() {
    # Select only rows from authenticated indexes, never installed-status data.
    /usr/bin/awk -F '\t' -v p="$1" -v v="$2" '
        $1==p && $2==v { k=$3 FS $4 FS $5 FS $6; if (n && k!=identity) exit 51; if (!n++) { identity=k; row=$0 } }
        END { if (!n) exit 51; print row }
    ' "$OUT/signed-packages.tsv" > "$OUT/selected-record.tsv" || fail
}
substep=tool_presence
for tool in /usr/bin/id /usr/bin/uname /usr/bin/awk /usr/bin/sed /usr/bin/grep /usr/bin/cat \
    /usr/bin/sha256sum /usr/bin/stat /usr/bin/cp /usr/bin/mkdir /usr/bin/chmod \
    /usr/bin/sort /usr/bin/cmp /usr/bin/find /usr/bin/rm /usr/bin/date /usr/bin/tar \
    /usr/bin/dpkg /usr/bin/dpkg-query /usr/bin/dpkg-deb /usr/bin/gpgv \
    /usr/bin/apt-get /usr/bin/apt-cache /usr/bin/apt-config /usr/lib/apt/apt-helper; do
    [ -x "$tool" ] || fail
done
substep=platform_identity
[ "$(/usr/bin/id -u)" = 0 ] || fail
[ "$(/usr/bin/uname -m)" = x86_64 ] || fail
[ "$(/usr/bin/dpkg --print-architecture)" = amd64 ] || fail
[ -z "$(/usr/bin/dpkg --print-foreign-architectures)" ] || fail
/usr/bin/grep -qx 'ID=ubuntu' /etc/os-release || fail
/usr/bin/grep -qx 'VERSION_ID="22.04"' /etc/os-release || fail
substep=base_keyring
[ -f /usr/share/keyrings/ubuntu-archive-keyring.gpg ] || fail
[ ! -L /usr/share/keyrings/ubuntu-archive-keyring.gpg ] || fail
[ ! -e "$OUT" ] && [ ! -L "$OUT" ] || fail
substep=helper_presence
for helper in parse_release.awk parse_packages.awk parse_simulation.awk parse_uris.awk parse_status.awk verify_delta.awk parse_ca_members.awk verify_ca_pem.awk check_python_prerequisites.py provision_system_ca.py; do
    [ -f "$HELPERS/$helper" ] && [ ! -L "$HELPERS/$helper" ] || fail
done
substep=evidence_workspace
/usr/bin/mkdir -m 755 "$OUT"
started=true
/usr/bin/mkdir -m 700 "$OUT/logs" "$OUT/empty-gnupg"
/usr/bin/mkdir -p "$OUT/repo/dists" "$OUT/lists/partial" "$OUT/archives/partial" \
    "$OUT/payloads" "$OUT/empty-conf" "$OUT/empty-sources" "$OUT/empty-prefs" "$OUT/empty-trusted" "$OUT/provisioning"
/usr/bin/cp "$HELPERS/bootstrap.sh" "$OUT/provisioning/bootstrap.sh"
substep=helper_presence
for helper in parse_release.awk parse_packages.awk parse_simulation.awk parse_uris.awk parse_status.awk verify_delta.awk parse_ca_members.awk verify_ca_pem.awk check_python_prerequisites.py provision_system_ca.py; do
    /usr/bin/cp "$HELPERS/$helper" "$OUT/provisioning/$helper"
done
substep=base_tool_hashes
/usr/bin/sha256sum /bin/sh /usr/bin/apt-get /usr/bin/apt-cache /usr/bin/apt-config \
    /usr/lib/apt/apt-helper /usr/bin/dpkg /usr/bin/dpkg-query /usr/bin/dpkg-deb \
    /usr/bin/gpgv /usr/bin/tar /usr/bin/awk > "$OUT/base-tools.sha256"
/usr/bin/cp /var/lib/dpkg/status "$OUT/base-status"
/usr/bin/cp /etc/os-release "$OUT/base-os-release"
/usr/bin/cp /usr/share/keyrings/ubuntu-archive-keyring.gpg "$OUT/base-keyring.gpg"
/usr/bin/sha256sum "$OUT/base-keyring.gpg" > "$OUT/base-keyring.sha256"
/usr/bin/date -u '+%Y-%m-%dT%H:%M:%SZ' > "$OUT/start-time-utc"
substep=apt_version
run base-apt-version /usr/bin/apt-get --version
/usr/bin/grep -q '^apt 2\.4\.' "$OUT/logs/base-apt-version.log" || fail
substep=base_audit
run base-dpkg-audit /usr/bin/dpkg --audit
[ ! -s "$OUT/logs/base-dpkg-audit.log" ] || fail
substep=base_inventory
run base-dpkg-query /usr/bin/dpkg-query -W
/usr/bin/awk -f "$HELPERS/parse_status.awk" "$OUT/base-status" > "$OUT/base.tsv" || fail

# No base image apt.conf hooks, mirror, proxy, preferences, or trusted keys leak
# into this transaction. Its keyring is an immutable copy of the pinned base's.
substep=apt_configuration
/usr/bin/cat > "$OUT/apt.conf" <<'EOF'
Dir::Etc::main "/dev/null";
Dir::Etc::parts "/opt/urp-bootstrap-evidence/empty-conf";
Dir::Etc::sourcelist "/opt/urp-bootstrap-evidence/sources.list";
Dir::Etc::sourceparts "/opt/urp-bootstrap-evidence/empty-sources";
Dir::Etc::preferences "/dev/null";
Dir::Etc::preferencesparts "/opt/urp-bootstrap-evidence/empty-prefs";
Dir::Etc::trusted "/dev/null";
Dir::Etc::trustedparts "/opt/urp-bootstrap-evidence/empty-trusted";
Dir::State::lists "/opt/urp-bootstrap-evidence/lists";
Dir::State::status "/var/lib/dpkg/status";
Dir::State::extended_states "/opt/urp-bootstrap-evidence/extended_states";
Dir::Cache::archives "/opt/urp-bootstrap-evidence/archives";
Dir::Cache::pkgcache "/opt/urp-bootstrap-evidence/pkgcache.bin";
Dir::Cache::srcpkgcache "/opt/urp-bootstrap-evidence/srcpkgcache.bin";
Dir::Log "/opt/urp-bootstrap-evidence/logs";
APT::Architecture "amd64";
APT::Install-Recommends "false";
APT::Install-Suggests "false";
APT::Get::Always-Include-Phased-Updates "true";
APT::Get::AllowUnauthenticated "false";
APT::Get::allow-Downgrades "false";
APT::Get::allow-Remove-Essential "false";
APT::Get::allow-Change-Held-Packages "false";
APT::Get::AutomaticRemove "false";
APT::Keep-Downloaded-Packages "true";
Binary::apt-get::APT::Keep-Downloaded-Packages "true";
Acquire::AllowInsecureRepositories "false";
Acquire::AllowDowngradeToInsecureRepositories "false";
Acquire::Check-Date "true";
Acquire::Check-Valid-Until "true";
Acquire::Max-FutureTime "10";
Acquire::http::AllowRedirect "false";
Acquire::https::AllowRedirect "false";
Acquire::https::Verify-Peer "true";
Acquire::https::Verify-Host "true";
Acquire::https::CaInfo "/opt/urp-bootstrap-evidence/bootstrap-ca.pem";
Acquire::Languages "none";
Acquire::Retries "0";
Acquire::http::Timeout "60";
Acquire::https::Timeout "60";
EOF
APT_CONFIG="$OUT/apt.conf"; export APT_CONFIG
: > "$OUT/sources.list"
: > "$OUT/signed-packages.tsv"
phase=public_ca_seed
substep=entry
# Offline trust: the initial archive keyring comes exclusively from the pinned
# image; all supplied inputs must match independently reviewed fixed hashes.
# No supplied path or archive member is executed, installed, or extracted onto /.
/usr/bin/mkdir "$OUT/ca-seed"
substep=seed_copy
for seed in jammy-security.InRelease jammy-security.main.amd64.Packages.xz ca-certificates.deb; do
    [ -f "$SEED/$seed" ] && [ ! -L "$SEED/$seed" ] || fail
    /usr/bin/cp "$SEED/$seed" "$OUT/ca-seed/$seed"
done
substep=release_bytes
hash_check "$OUT/ca-seed/jammy-security.InRelease" eaa0a9cb0ad5ea72dcaf84e81a1f13c82a5afbb08700bee189b9b7e2c638c5a9
[ "$(/usr/bin/stat -c %s "$OUT/ca-seed/jammy-security.InRelease")" = 128927 ] || fail
substep=release_signature
run ca-seed-signature /usr/bin/gpgv --homedir "$OUT/empty-gnupg" --status-fd=1 --keyring "$OUT/base-keyring.gpg" "$OUT/ca-seed/jammy-security.InRelease"
substep=release_signer
/usr/bin/awk -v signer="$SIGNER" '
    $1=="[GNUPG:]" && $2=="VALIDSIG" { if($3!=signer) exit 52; n++ }
    $1=="[GNUPG:]" && $2 ~ /^(BADSIG|ERRSIG|EXPSIG|EXPKEYSIG|REVKEYSIG|NO_PUBKEY)$/ { exit 52 }
    END { if(n!=1) exit 52 }
' "$OUT/logs/ca-seed-signature.log" || fail
substep=release_parse
/usr/bin/awk -v suite=jammy-security -f "$HELPERS/parse_release.awk" "$OUT/ca-seed/jammy-security.InRelease" > "$OUT/ca-seed/indexes.tsv" || fail
substep=index_identity
/usr/bin/awk -F '\t' '$3=="main/binary-amd64/Packages.xz" {if($1!="193e21d53e0d7fbf97fed37314da94d75223d4575b769aa95891d8ea1592471a" || $2!=3643160) exit 57; n++} END {if(n!=1) exit 57}' "$OUT/ca-seed/indexes.tsv" || fail
substep=index_bytes
hash_check "$OUT/ca-seed/jammy-security.main.amd64.Packages.xz" 193e21d53e0d7fbf97fed37314da94d75223d4575b769aa95891d8ea1592471a
[ "$(/usr/bin/stat -c %s "$OUT/ca-seed/jammy-security.main.amd64.Packages.xz")" = 3643160 ] || fail
substep=index_decode
/usr/lib/apt/apt-helper cat-file "$OUT/ca-seed/jammy-security.main.amd64.Packages.xz" > "$OUT/ca-seed/Packages" 2> "$OUT/logs/ca-seed-decompress.log" || fail
substep=package_parse
/usr/bin/awk -v suite=jammy-security -v component=main -f "$HELPERS/parse_packages.awk" "$OUT/ca-seed/Packages" > "$OUT/ca-seed/packages.tsv" || fail
substep=package_identity
/usr/bin/awk -F '\t' '
    $1=="ca-certificates" && $2=="20260816ubuntu0.22.04.1" {
        if($3!="all" || $4!="pool/main/c/ca-certificates/ca-certificates_20260816ubuntu0.22.04.1_all.deb" || $5!=139540 || $6!="29ca037f49f7a4b41d67ad72946c2e541f3ae52d8f6ab1495d2af09aa391144b") exit 58
        n++; print
    } END {if(n!=1) exit 58}
' "$OUT/ca-seed/packages.tsv" > "$OUT/ca-seed/selected-package.tsv" || fail
substep=archive_bytes
hash_check "$OUT/ca-seed/ca-certificates.deb" 29ca037f49f7a4b41d67ad72946c2e541f3ae52d8f6ab1495d2af09aa391144b
[ "$(/usr/bin/stat -c %s "$OUT/ca-seed/ca-certificates.deb")" = 139540 ] || fail
# dpkg-deb only decodes a data tar here. It does not run maintainer scripts.
substep=archive_decode
/usr/bin/dpkg-deb --fsys-tarfile "$OUT/ca-seed/ca-certificates.deb" > "$OUT/ca-seed/data.tar" 2> "$OUT/logs/ca-seed-data-tar.log" || fail
substep=member_listing
/usr/bin/tar --list --verbose --numeric-owner --full-time --quoting-style=literal --file "$OUT/ca-seed/data.tar" > "$OUT/ca-seed/members.txt" 2> "$OUT/logs/ca-seed-tar-list.log" || fail
substep=member_selection
/usr/bin/awk -f "$HELPERS/parse_ca_members.awk" "$OUT/ca-seed/members.txt" > "$OUT/ca-seed/cert-members.txt" || fail
: > "$OUT/bootstrap-ca.pem"
substep=certificate_data
while IFS= read -r member; do
    substep=certificate_data
    /usr/bin/tar --extract --to-stdout --file "$OUT/ca-seed/data.tar" -- "$member" > "$OUT/ca-seed/piece.pem" 2> "$OUT/logs/ca-seed-tar-read.log" || fail
    substep=certificate_pem
    /usr/bin/awk -f "$HELPERS/verify_ca_pem.awk" "$OUT/ca-seed/piece.pem" || fail
    substep=certificate_bundle
    /usr/bin/cat "$OUT/ca-seed/piece.pem" >> "$OUT/bootstrap-ca.pem"
    printf '\n' >> "$OUT/bootstrap-ca.pem"
done < "$OUT/ca-seed/cert-members.txt"
substep=bundle_hash
/usr/bin/rm "$OUT/ca-seed/piece.pem"
/usr/bin/sha256sum "$OUT/bootstrap-ca.pem" > "$OUT/bootstrap-ca.sha256"
substep=base_unchanged
/usr/bin/cmp -s /var/lib/dpkg/status "$OUT/base-status" || fail
phase=signed_metadata
substep=entry
for suite in jammy jammy-updates jammy-security; do
    substep=entry
    case "$suite" in
        jammy) digest=c14060cd8c6d625874dfcb9523a35a395bf4865c28b6b6a82569ef326fe92dc6; size=270087 ;;
        jammy-updates) digest=34beb69f860de8eb98f2315b4c38c2542b1b005153652e399d898c892431adda; size=128049 ;;
        jammy-security) digest=eaa0a9cb0ad5ea72dcaf84e81a1f13c82a5afbb08700bee189b9b7e2c638c5a9; size=128927 ;;
    esac
    release="$OUT/repo/dists/$suite/InRelease"
    /usr/bin/mkdir -p "$OUT/repo/dists/$suite"
    substep=release_fetch
    fetch "$suite-release-download" "$ORIGIN/dists/$suite/InRelease" "$release" "$digest" "$size"
    substep=release_signature
    run "$suite-signature" /usr/bin/gpgv --homedir "$OUT/empty-gnupg" --status-fd=1 --keyring "$OUT/base-keyring.gpg" "$release"
    substep=release_signer
    /usr/bin/awk -v signer="$SIGNER" '
        $1=="[GNUPG:]" && $2=="VALIDSIG" { if ($3!=signer) exit 52; n++ }
        $1=="[GNUPG:]" && $2 ~ /^(BADSIG|ERRSIG|EXPSIG|EXPKEYSIG|REVKEYSIG|NO_PUBKEY)$/ { exit 52 }
        END { if (n!=1) exit 52 }
    ' "$OUT/logs/$suite-signature.log" || fail
    substep=release_parse
    /usr/bin/awk -v suite="$suite" -f "$HELPERS/parse_release.awk" "$release" > "$OUT/$suite-indexes.tsv" || fail
    while IFS="$(printf '\t')" read -r index_hash index_size relative; do
        substep=entry
        component=${relative%%/*}
        /usr/bin/mkdir -p "$OUT/repo/dists/$suite/$component/binary-amd64"
        index="$OUT/repo/dists/$suite/$relative"
        substep=index_fetch
        fetch "$suite-$component-index-download" "$ORIGIN/dists/$suite/$relative" "$index" "$index_hash" "$index_size"
        # apt-helper uses libapt's built-in decompression; no xz package needed.
        substep=index_decode
        /usr/lib/apt/apt-helper cat-file "$index" > "$OUT/$suite-$component-Packages" 2> "$OUT/logs/$suite-$component-decompress.log" || fail
        substep=index_parse
        /usr/bin/awk -v suite="$suite" -v component="$component" -f "$HELPERS/parse_packages.awk" \
            "$OUT/$suite-$component-Packages" >> "$OUT/signed-packages.tsv" || fail
    done < "$OUT/$suite-indexes.tsv"
    substep=source_registration
    printf 'deb [arch=amd64 signed-by=%s/base-keyring.gpg by-hash=no] file:%s/repo %s main universe\n' "$OUT" "$OUT" "$suite" >> "$OUT/sources.list"
done
phase=apt_metadata_check
substep=entry
substep=configuration_dump
run effective-apt-config /usr/bin/apt-config dump
# APT independently checks signed index hashes, signed dates/Valid-Until, and
# repository authentication. A fixed old snapshot may expire: stop, never waive.
substep=apt_update
run apt-update /usr/bin/apt-get -o APT::Update::Error-Mode=any update
substep=base_unchanged
/usr/bin/cmp -s /var/lib/dpkg/status "$OUT/base-status" || fail

phase=signed_root_selection
substep=entry
: > "$OUT/roots.tsv"
for package in python3 python3-apt ca-certificates; do
    # Ignore installed-status versions when selecting signed snapshot candidates.
    substep=candidate_policy
    run "$package-policy" /usr/bin/apt-cache -o Dir::State::status=/dev/null \
        -o Dir::Cache::pkgcache= -o Dir::Cache::srcpkgcache= policy "$package"
    substep=candidate_parse
    version=$(/usr/bin/awk '$1=="Candidate:" { if (NF!=2 || n++) exit 53; v=$2 } END { if(n!=1 || v !~ /^[A-Za-z0-9.+:~_-]+$/) exit 53; print v }' "$OUT/logs/$package-policy.log") || fail
    substep=signed_identity
    lookup "$package" "$version"
    substep=root_record
    printf '%s\t%s\n' "$package" "$version" >> "$OUT/roots.tsv"
done
set --
while IFS="$(printf '\t')" read -r package version; do set -- "$@" "$package=$version"; done < "$OUT/roots.tsv"
phase=transaction_plan
substep=entry
substep=initial_simulation
run initial-simulation /usr/bin/apt-get --simulate --no-install-recommends --no-remove install "$@"
substep=simulation_parse
/usr/bin/awk -f "$HELPERS/parse_simulation.awk" "$OUT/logs/initial-simulation.log" > "$OUT/transaction.raw.tsv" || fail
: > "$OUT/transaction.tsv"
while IFS="$(printf '\t')" read -r package version old; do
    substep=signed_identity
    lookup "$package" "$version"
    IFS="$(printf '\t')" read -r signed_name signed_version architecture filename bytes digest suite component < "$OUT/selected-record.tsv"
    substep=installed_identity
    installed=$(/usr/bin/awk -F '\t' -v p="$package" '$1==p { print $2 " " $3 " " $4 }' "$OUT/base.tsv")
    substep=action_checks
    if [ "$old" = - ]; then
        [ -z "$installed" ] || fail; action=install
    else
        [ "$installed" = "$architecture $old install" ] || fail
        /usr/bin/dpkg --compare-versions "$version" gt "$old" || fail
        action=upgrade
    fi
    substep=transaction_record
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$package" "$architecture" "$version" "$old" "$action" "$filename" "$bytes" "$digest" "$suite" "$component" >> "$OUT/transaction.tsv"
done < "$OUT/transaction.raw.tsv"
set --
while IFS="$(printf '\t')" read -r package architecture version rest; do set -- "$@" "$package=$version"; done < "$OUT/transaction.tsv"
if [ "$#" -gt 0 ]; then
    substep=pinned_simulation
    run pinned-simulation /usr/bin/apt-get --simulate --no-install-recommends --no-remove install "$@"
    substep=simulation_parse
    /usr/bin/awk -f "$HELPERS/parse_simulation.awk" "$OUT/logs/pinned-simulation.log" > "$OUT/pinned.raw.tsv" || fail
    substep=simulation_compare
    /usr/bin/sort "$OUT/transaction.raw.tsv" > "$OUT/initial.sorted.tsv"
    substep=simulation_compare
    /usr/bin/sort "$OUT/pinned.raw.tsv" > "$OUT/pinned.sorted.tsv"
    /usr/bin/cmp -s "$OUT/initial.sorted.tsv" "$OUT/pinned.sorted.tsv" || fail
    substep=archive_names
    run archive-cache-names /usr/bin/apt-get --print-uris --download-only --no-install-recommends --no-remove install "$@"
    substep=archive_names_parse
    /usr/bin/awk -f "$HELPERS/parse_uris.awk" "$OUT/logs/archive-cache-names.log" > "$OUT/archive-cache-names.tsv" || fail
    substep=archive_names_match
    /usr/bin/awk -F '\t' '
        FILENAME==ARGV[1] { expected[$6]=$7; next }
        { if (NF!=3 || !($1 in expected) || expected[$1]!=$3 || seen[$1]++) exit 54 }
        END { for (p in expected) if (!seen[p]) exit 54 }
    ' "$OUT/transaction.tsv" "$OUT/archive-cache-names.tsv" || fail
    phase=archive_authentication
    substep=entry
    while IFS="$(printf '\t')" read -r package architecture version old action filename bytes digest suite component; do
        payload="$OUT/payloads/$digest.deb"
        substep=archive_fetch
        fetch "$package-archive-download" "$ORIGIN/$filename" "$payload" "$digest" "$bytes"
        substep=archive_control
        run "$package-archive-control" /usr/bin/dpkg-deb --field "$payload"
        substep=control_identity
        /usr/bin/awk -v p="$package" -v v="$version" -v a="$architecture" '
            /^Package: / { if (pn++ || $0!="Package: " p) exit 55 }
            /^Version: / { if (vn++ || $0!="Version: " v) exit 55 }
            /^Architecture: / { if (an++ || $0!="Architecture: " a) exit 55 }
            END { if(pn!=1 || vn!=1 || an!=1) exit 55 }
        ' "$OUT/logs/$package-archive-control.log" || fail
        substep=cache_name
        cache_name=$(/usr/bin/awk -F '\t' -v f="$filename" '$1==f {print $2}' "$OUT/archive-cache-names.tsv")
        [ -n "$cache_name" ] && [ ! -e "$OUT/archives/$cache_name" ] || fail
        substep=cache_copy
        /usr/bin/cp "$payload" "$OUT/archives/$cache_name"
        substep=cache_hash
        hash_check "$OUT/archives/$cache_name" "$digest"
    done < "$OUT/transaction.tsv"
    phase=install_bootstrap
    substep=entry
    substep=base_unchanged
    /usr/bin/cmp -s /var/lib/dpkg/status "$OUT/base-status" || fail
    substep=offline_simulation
    run no-download-simulation /usr/bin/apt-get --simulate --no-download --no-install-recommends --no-remove install "$@"
    substep=offline_parse
    /usr/bin/awk -f "$HELPERS/parse_simulation.awk" "$OUT/logs/no-download-simulation.log" > "$OUT/no-download.raw.tsv" || fail
    substep=offline_compare
    /usr/bin/sort "$OUT/no-download.raw.tsv" > "$OUT/no-download.sorted.tsv"
    /usr/bin/cmp -s "$OUT/initial.sorted.tsv" "$OUT/no-download.sorted.tsv" || fail
    substep=package_install
    run install /usr/bin/apt-get --assume-yes --no-download --no-install-recommends --no-remove install "$@"
fi
phase=post_install_verification
substep=entry
substep=status_snapshot
/usr/bin/cp /var/lib/dpkg/status "$OUT/post-status"
substep=status_parse
/usr/bin/awk -f "$HELPERS/parse_status.awk" "$OUT/post-status" > "$OUT/post.tsv" || fail
substep=transaction_delta
/usr/bin/awk -f "$HELPERS/verify_delta.awk" "$OUT/base.tsv" "$OUT/transaction.tsv" "$OUT/post.tsv" || fail
substep=root_versions
while IFS="$(printf '\t')" read -r package version; do
    /usr/bin/awk -F '\t' -v p="$package" -v v="$version" '$1==p && $3==v {n++} END { if(n!=1) exit 56 }' "$OUT/post.tsv" || fail
done < "$OUT/roots.tsv"
substep=apt_check
run post-apt-check /usr/bin/apt-get check
substep=dpkg_audit
run post-dpkg-audit /usr/bin/dpkg --audit
substep=audit_clean
[ ! -s "$OUT/logs/post-dpkg-audit.log" ] || fail
substep=system_ca_defaults
system_ca_result=0
/usr/bin/python3 -I -B "$HELPERS/provision_system_ca.py" --install-default-alias > "$OUT/logs/system-ca-default.log" 2>&1 || system_ca_result=$?
case "$system_ca_result" in
    0) ;;
    71)
        substep=system_ca_layout
        fail
        ;;
    72)
        substep=system_ca_bundle
        fail
        ;;
    73)
        substep=system_ca_usable
        fail
        ;;
    74)
        substep=system_ca_default_path
        fail
        ;;
    75)
        substep=system_ca_alias_conflict
        fail
        ;;
    76)
        substep=system_ca_alias_write
        fail
        ;;
    77)
        substep=system_ca_record
        fail
        ;;
    78)
        substep=system_ca_execution
        fail
        ;;
    *)
        substep=system_ca_unknown
        fail
        ;;
esac
substep=python_prerequisites
prerequisite_result=0
/usr/bin/python3 -I -B "$HELPERS/check_python_prerequisites.py" > "$OUT/logs/python-apt-prerequisites.log" 2>&1 || prerequisite_result=$?
case "$prerequisite_result" in
    0) ;;
    61)
        substep=python_import_sys
        fail
        ;;
    62)
        substep=python_import_apt_pkg
        fail
        ;;
    63)
        substep=python_import_ssl
        fail
        ;;
    64)
        substep=python_version_check
        fail
        ;;
    65)
        substep=python_version_mismatch
        fail
        ;;
    66)
        substep=apt_version_check
        fail
        ;;
    67)
        substep=apt_version_mismatch
        fail
        ;;
    68)
        substep=ca_path_check
        fail
        ;;
    69)
        substep=ca_file_missing
        fail
        ;;
    *)
        substep=python_execution
        fail
        ;;
esac
substep=final_snapshot
/usr/bin/cp /var/lib/dpkg/status "$OUT/final-status"
substep=final_unchanged
/usr/bin/cmp -s "$OUT/post-status" "$OUT/final-status" || fail
substep=timestamp
/usr/bin/date -u '+%Y-%m-%dT%H:%M:%SZ' > "$OUT/end-time-utc"
phase=receipt
substep=entry
substep=receipt_write
/usr/bin/cat > "$OUT/bootstrap.json" <<'EOF'
{"schema":1,"status":"bootstrap-installed-transaction-verified","snapshot":"20261021T000000Z","bootstrap_python":"system-python3.10-not-extension-ABI","expected_base_manifest":"sha256:98510ac07bb4545e389be502a8918b385c018bfe4ceeb5ac7e0375c782268b04","actual_base_identity_verified_here":false,"transaction_authenticated":true,"full_closure_verified":false,"native_build_run":false,"abi_or_geometry_acceptance":false,"next_gate":"Authenticate the complete bootstrap and toolchain Depends/Pre-Depends/Essential closure, retained base packages and actual owned bytes before native preparation acceptance."}
EOF
substep=receipt_inventory
(
    cd "$OUT"
    /usr/bin/find . -type f ! -name SHA256SUMS ! -name .file-list -print > .file-list
    /usr/bin/sort -o .file-list .file-list
    while IFS= read -r path; do /usr/bin/sha256sum "$path"; done < .file-list > SHA256SUMS
    /usr/bin/rm .file-list
)
completed=true
printf '%s\n' 'SAFE_BOOTSTRAP_DIAGNOSTIC:BOOTSTRAP_TRANSACTION_VERIFIED_FULL_CLOSURE_PENDING'
