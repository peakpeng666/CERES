# Inputs: base.tsv, transaction.tsv, post.tsv. Transaction columns are documented.
function reject() { bad=1; exit 46 }
BEGIN { FS="\t" }
FILENAME==ARGV[1] { if (NF!=4 || $1 in expected) reject(); expected[$1]=$2 FS $3 FS $4; next }
FILENAME==ARGV[2] {
    if (NF!=10 || changed[$1]++) reject()
    if ($5=="upgrade") { if (!($1 in expected) || expected[$1]!=$2 FS $4 FS "install") reject() }
    else if ($5=="install") { if ($1 in expected || $4!="-") reject() }
    else reject()
    expected[$1]=$2 FS $3 FS "install"; next
}
FILENAME==ARGV[3] {
    if (NF!=4 || !($1 in expected) || seen[$1]++ || expected[$1]!=$2 FS $3 FS $4) reject()
}
END { if (bad) exit 46; for (p in expected) if (!seen[p]) exit 46 }
