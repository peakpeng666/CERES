# GNU tar --list --verbose --numeric-owner --full-time output for a hash-pinned
# Ubuntu .deb data archive. Only named regular certificate members are emitted.
function reject() { bad=1; exit 47 }
BEGIN { prefix="./usr/share/ca-certificates/mozilla/" }
index($0,prefix) {
    # The directory record is not selected; no path is extracted to filesystem.
    if ($6==prefix && substr($1,1,1)=="d" && NF==6) next
    if (NF!=6 || substr($1,1,1)!="-" || index($6,prefix)!=1 || $3 !~ /^[0-9]+$/ || $3+0<=0 || $3+0>65536) reject()
    name=substr($6,length(prefix)+1)
    # Exact non-ASCII basename present in the hash-pinned Mozilla CA archive.
    # Listing uses literal quoting; no generic Unicode/path whitelist expansion.
    if ((name !~ /^[A-Za-z0-9_+().,-]+\.crt$/ && name != "NetLock_Arany_=Class_Gold=_Főtanúsítvány.crt") || name ~ /\.\./ || seen[$6]++) reject()
    if (++n>512) reject()
    print $6
}
END { if(bad || !n) exit 47 }
