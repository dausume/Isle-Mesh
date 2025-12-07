# Test api DNS calls

## Fake mDNS Call From Fake Proxy
### Mocks calling from the backend.mesh-app.local (as though this is from the nginx-proxy) and going to port 8100 on the container.
### Forces it to resolve without using DNS or mDNS, to mock it resolving successfully even if DNS is not setup.
curl https://backend.mesh-app.local:8100 \
  --resolve backend.mesh-app.local:8100:127.0.0.1 \
  --cert ssl/certs/mesh-app.crt \
  --key ssl/keys/mesh-app.key \
  --cacert ssl/certs/backend.mesh-app.crt \
  -v

## Fake mDNS Call through Real Proxy
### Mock call to the nginx proxy, trying to route into the backend, mocks resolving via mDNS even without it being setup.
### We must set our app to trust the mesh-app.crt which is the cert for the proxy, this emulates us connecting to the
### mesh-app proxy, being prompted by our browser to trust the proxy, then the proxy performs mTLS with the server.
curl https://backend.mesh-app.local \
  --resolve backend.mesh-app.local:443:127.0.0.1 \
  --cacert ssl/certs/mesh-app.crt \
  -v

## Real mDNS Call to Backend through Real Proxy
### Mock calling the nginx proxy using real mDNS to hit the backend.
curl https://backend.mesh-app.local \
  --cacert ssl/certs/mesh-app.crt \
  -v
