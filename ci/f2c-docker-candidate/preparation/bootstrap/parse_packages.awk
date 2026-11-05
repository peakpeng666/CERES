# Emit minimal lookup rows; preserve the complete authenticated Packages files.
function reject() { bad=1; exit 42 }
function flush( p,v,a,f,s,h) {
    if (!n) return
    p=r["Package"]; v=r["Version"]; a=r["Architecture"]; f=r["Filename"]; s=r["Size"]; h=r["SHA256"]
    if (p !~ /^[a-z0-9][a-z0-9+.-]+$/ || v !~ /^[A-Za-z0-9.+:~_-]+$/ || (a!="amd64" && a!="all")) reject()
    if (f !~ /^pool\/(main|universe)\/[A-Za-z0-9+._\/~:-]+\.deb$/ || f ~ /(^|\/)\.\.(\/|$)/ || f ~ /\/\//) reject()
    if (s !~ /^[0-9]+$/ || s+0<=0 || length(h)!=64 || h !~ /^[0-9a-f]+$/) reject()
    print p,v,a,f,s,h,suite,component
    for (k in r) delete r[k]; n=0
}
BEGIN { OFS="\t" }
/^$/ { flush(); next }
/^[ \t]/ { next }
{ k=$0; sub(/:.*/,"",k); v=$0; sub(/^[^:]*: */,"",v)
  if (k=="Package" || k=="Version" || k=="Architecture" || k=="Filename" || k=="Size" || k=="SHA256") {
      if (k in r) reject(); r[k]=v; n++
  }
}
END { if (bad) exit 42; flush() }
