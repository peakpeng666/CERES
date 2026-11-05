# Certificate public data only. Private-key/other PEM objects never enter bundle.
BEGIN { state=0; bad=0 }
{ sub(/\r$/, "") }
$0=="-----BEGIN CERTIFICATE-----" { if(state!=0) {bad=1; exit 48}; state=1; next }
$0=="-----END CERTIFICATE-----" { if(state!=1 || !n) {bad=1; exit 48}; state=2; next }
state==1 { if($0 !~ /^[A-Za-z0-9+\/=]+$/ || length($0)>76) {bad=1; exit 48}; n++; next }
$0!="" {bad=1; exit 48}
END { if(bad || state!=2) exit 48 }
