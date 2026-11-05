# APT print-uris is used for cache filenames ONLY, never as network authority.
function reject() { bad=1; exit 44 }
BEGIN { OFS="\t"; prefix="file:/opt/urp-bootstrap-evidence/repo/" }
/^\047/ {
    if (NF!=4 || $1 !~ /\047$/) reject()
    u=substr($1,2,length($1)-2)
    sub(/^file:\/\/\//,"file:/",u)
    gsub(/%3[aA]/,":",u); gsub(/%2[bB]/,"+",u); gsub(/%7[eE]/,"~",u)
    if (index(u,prefix)!=1 || u ~ /%/) reject()
    f=substr(u,length(prefix)+1); c=$2
    if (f !~ /^pool\/(main|universe)\/[A-Za-z0-9+._\/~:-]+\.deb$/ || f ~ /(^|\/)\.\.(\/|$)/ || f ~ /\/\//) reject()
    if (c !~ /^[A-Za-z0-9+._%~:-]+\.deb$/ || c ~ /\.\./ || $3 !~ /^[0-9]+$/ || $3+0<=0 || seen[c]++ || paths[f]++) reject()
    print f,c,$3
}
END { if (bad) exit 44 }
