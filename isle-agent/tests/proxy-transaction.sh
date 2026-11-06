#!/bin/sh
# Run only in an isolated disposable container with --network none.
set -eu
mkdir -p /etc/nginx/ssl/certs /etc/nginx/ssl/keys /etc/nginx/configs
cp /source/isle-vlan-agent/nginx.conf /etc/nginx/nginx.conf
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -keyout /tmp/ca.key -out /tmp/ca.crt -subj /CN=IsolatedTestCA >/dev/null 2>&1
for domain in api.polari.isle unrelated.isle; do
 openssl req -new -newkey rsa:2048 -nodes -keyout "/etc/nginx/ssl/keys/$domain.key" -out /tmp/server.csr -subj "/CN=$domain" >/dev/null 2>&1
 printf 'subjectAltName=DNS:%s\n' "$domain" > /tmp/extensions
 openssl x509 -req -in /tmp/server.csr -CA /tmp/ca.crt -CAkey /tmp/ca.key -CAcreateserial -days 1 -extfile /tmp/extensions -out "/etc/nginx/ssl/certs/$domain.crt" >/dev/null 2>&1
done
cat > /tmp/registry.json <<'JSON'
{"apps":{"backend":{"domain":"api.polari.isle","services":[{"name":"backend","container":"127.0.0.1","port":9,"protocol":"http"}]},"other":{"domain":"unrelated.isle","services":[{"name":"other","container":"127.0.0.1","port":9,"protocol":"http"}]}}}
JSON
G=/source/isle-vlan-agent/generate-nginx-configs.sh
sh "$G" /tmp/registry.json /etc/nginx/configs
nginx
probe() { curl --http1.1 --silent --show-error --cacert /tmp/ca.crt --resolve "$1:443:127.0.0.1" --max-time 2 "https://$1/" >/dev/null; }
probe api.polari.isle; probe unrelated.isle
before=$(sha256sum /etc/nginx/configs/isle-generated.conf)
inode=$(stat -c '%i:%Y' /etc/nginx/configs/isle-generated.conf)
sh "$G" /tmp/registry.json /etc/nginx/configs
[ "$inode" = "$(stat -c '%i:%Y' /etc/nginx/configs/isle-generated.conf)" ]
# Invalid JSON and invalid nginx candidate must never publish partial state.
printf '{broken' >/tmp/invalid.json
if sh "$G" /tmp/invalid.json /etc/nginx/configs; then exit 1; fi
jq '.apps.backend.services[0].port="invalid;"' /tmp/registry.json >/tmp/invalid.json
if sh "$G" /tmp/invalid.json /etc/nginx/configs; then exit 1; fi
[ "$before" = "$(sha256sum /etc/nginx/configs/isle-generated.conf)" ]
nginx -t; nginx -s reload; probe api.polari.isle
# No SNI and unknown SNI fail TLS instead of receiving another app certificate.
if openssl s_client -connect 127.0.0.1:443 -noservername </dev/null >/tmp/no-sni 2>&1; then exit 1; fi
if openssl s_client -connect 127.0.0.1:443 -servername absent.isle </dev/null >/tmp/wrong-sni 2>&1; then exit 1; fi
# Repeated complete publications/reloads while two named TLS clients connect.
( i=0; while [ "$i" -lt 20 ]; do probe api.polari.isle; probe unrelated.isle; i=$((i+1)); done ) &
client=$!
i=0
while [ "$i" -lt 3 ]; do
 sh "$G" /tmp/registry.json /etc/nginx/configs
 nginx -t; nginx -s reload; i=$((i+1))
done
wait "$client"
nginx -s quit
printf 'PASS: two trusted names, no-op stability, invalid registry/config retention, unknown/no-SNI rejection, concurrent named TLS reloads\n'
