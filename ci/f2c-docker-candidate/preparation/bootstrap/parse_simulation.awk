# Native APT 2.4 LC_ALL=C simulation. Unsupported grammar is a blocker.
function reject() { bad=1; exit 43 }
BEGIN { OFS="\t" }
/^(Remv|Purg) / { reject() }
/^Inst([ \t]|$)/ {
    p=$2; sub(/:amd64$/, "", p)
    if (p !~ /^[a-z0-9][a-z0-9+.-]+$/ || seen[p]++) reject()
    old="-"; i=3
    if ($i ~ /^\[/) { old=$i; if (old !~ /^\[[A-Za-z0-9.+:~_-]+\]$/) reject(); old=substr(old,2,length(old)-2); i++ }
    v=$i; if (v !~ /^\([A-Za-z0-9.+:~_-]+$/) reject(); v=substr(v,2)
    if ($0 !~ / \[(amd64|all)\]\)$/) reject()
    print p,v,old
}
/^(Inst|Remv|Purg)([ \t]|$)/ && $1!="Inst" { reject() }
END { if (bad) exit 43 }
