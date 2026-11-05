function reject() { bad=1; exit 45 }
function flush( s,p,a,v,h) {
    if (!n) return
    s=r["Status"]
    if (s=="install ok installed" || s=="hold ok installed") {
        p=r["Package"]; a=r["Architecture"]; v=r["Version"]; h=(s=="hold ok installed" ? "hold" : "install")
        if (p !~ /^[a-z0-9][a-z0-9+.-]+$/ || (a!="amd64" && a!="all") || v !~ /^[A-Za-z0-9.+:~_-]+$/ || seen[p]++) reject()
        print p,a,v,h
    } else if (s!="deinstall ok config-files" && s!="purge ok not-installed" && s!="unknown ok not-installed") reject()
    for (k in r) delete r[k]; n=0
}
BEGIN { OFS="\t" }
/^$/ { flush(); next }
/^[ \t]/ { next }
{ k=$0; sub(/:.*/,"",k); v=$0; sub(/^[^:]*: */,"",v)
  if (k=="Package" || k=="Version" || k=="Architecture" || k=="Status") {
      if (k in r) reject(); r[k]=v; n++
  }
}
END { if (bad) exit 45; flush() }
