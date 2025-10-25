🔧 The Full Command

curl https://backend.mesh-app.local:8100 \
  --resolve backend.mesh-app.local:8100:127.0.0.1 \
  --cert ssl/certs/mesh-app.crt \
  --key ssl/keys/mesh-app.key \
  --cacert ssl/certs/backend.mesh-app.crt \
  -v

🧩 Line-by-Line Explanation
✅ https://backend.mesh-app.local:8100

This sets:

    Scheme: https = Use SSL/TLS

    Host (URI): backend.mesh-app.local = The Host: header sent to the server (and also used in SNI/TLS handshake)

    Port: 8100 = The destination port on the server

    🔐 This host must match a SAN or CN in the backend server’s TLS certificate.

✅ --resolve backend.mesh-app.local:8100:127.0.0.1

This overrides DNS resolution:

    Tells curl to resolve backend.mesh-app.local:8100 to 127.0.0.1 instead of using DNS/mDNS.

    Internally, it:

        Sends request to 127.0.0.1:8100

        Sets Host: backend.mesh-app.local in the request

        Uses backend.mesh-app.local as the SNI (Server Name Indication) for TLS

    🔎 Why use this? It allows you to test how requests would look as if they came from an mDNS-resolved name, even when you're not using mDNS routing.

✅ --cert ssl/certs/mesh-app.crt

This tells curl:

    Present this certificate to the server as part of the mutual TLS (mTLS) handshake.

    This must be the cert for the client, in this case the nginx proxy’s identity (e.g., mesh-app.local).

✅ --key ssl/keys/mesh-app.key

This is the private key corresponding to the cert above. It proves that you “own” the client cert being sent.
✅ --cacert ssl/certs/backend.mesh-app.crt

This is the CA certificate or self-signed cert of the server that you're connecting to.

    curl uses this to verify the server's certificate.

    If the cert the backend sends doesn’t match this, the connection is rejected.

✅ -v

Verbose mode: shows all connection details, including:

    TLS handshake steps

    Cert verification

    Request/response headers

❓ Should --resolve be necessary if mDNS is working?

If Avahi/mDNS is working fully, and backend.mesh-app.local can be resolved on your system:

    ✅ --resolve should not be needed.

    You should be able to run:

    curl https://backend.mesh-app.local:8100 \
      --cert ssl/certs/mesh-app.crt \
      --key ssl/keys/mesh-app.key \
      --cacert ssl/certs/backend.mesh-app.crt \
      -v

🔎 But if it fails to resolve...

It likely means:

    mDNS is working at the Avahi level, but not exposed through your system DNS resolver (glibc, systemd-resolved, nss-mdns, etc.)

    Firefox might resolve .local, but curl (linked against different libraries) may not.

So --resolve is a reliable way to mock mDNS for testing.
🔁 Summary
Component	Purpose
https://backend.mesh-app.local:8100	Target host+port with correct URI + TLS SNI
--resolve	Maps host manually to IP for testing/mDNS bypass
--cert + --key	Client TLS cert and private key for mTLS handshake
--cacert	Tells curl which CA cert to trust as the backend’s TLS cert
-v	Shows the full TLS negotiation and request details