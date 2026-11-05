# Parse only after successful gpgv and exact reviewed InRelease SHA256 checks.
function reject() { bad=1; exit 41 }
BEGIN { state=0; hashes=0; OFS="\t" }
{ sub(/\r$/, "") }
NR==1 { if ($0!="-----BEGIN PGP SIGNED MESSAGE-----") reject(); state=1; next }
state==1 { if ($0=="") state=2; next }
$0=="-----BEGIN PGP SIGNATURE-----" { state=3; next }
state!=2 { next }
{ sub(/^- /, "") }
/^[^ \t][^:]*:/ {
    key=$0; sub(/:.*/, "", key)
    value=$0; sub(/^[^:]*:[ \t]*/, "", value)
    if (key=="Origin" || key=="Suite" || key=="Codename" || key=="Architectures" || key=="Components") {
        if (seen[key]++) reject(); header[key]=value
    }
    hashes=(key=="SHA256"); next
}
hashes && /^[ \t]/ {
    if (NF!=3 || length($1)!=64 || $1 !~ /^[0-9a-f]+$/ || $2 !~ /^[0-9]+$/) reject()
    if ($3 ~ /^(main|universe)\/binary-amd64\/Packages\.xz$/) {
        if (seen_path[$3]++) reject(); digest[$3]=$1; size[$3]=$2
    }
}
END {
    if (bad) exit 41
    if (state!=3 || header["Origin"]!="Ubuntu" || header["Suite"]!=suite || header["Codename"]!="jammy") exit 41
    if (" " header["Architectures"] " " !~ / amd64 / || " " header["Components"] " " !~ / main / || " " header["Components"] " " !~ / universe /) exit 41
    a="main/binary-amd64/Packages.xz"; b="universe/binary-amd64/Packages.xz"
    if (seen_path[a]!=1 || seen_path[b]!=1) exit 41
    print digest[a],size[a],a; print digest[b],size[b],b
}
