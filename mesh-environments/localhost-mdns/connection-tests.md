# connection-tests.md

## Primary Domain
### HTTP
    curl -v --resolve backend.mesh-app.local:80:127.0.0.1 http://backend.mesh-app.local/
### HTTPS
    curl -vk --resolve mesh-app.local:443:127.0.0.1 https://mesh-app.local/

## Backend Sub-Domain
### HTTP
    curl -v --resolve backend.mesh-app.local:80:127.0.0.1 http://backend.mesh-app.local/
### HTTPS
    curl -vk --resolve backend.mesh-app.local:443:127.0.0.1 https://backend.mesh-app.local/

## Frontend Sub-Domain
### HTTP
    curl -v --resolve frontend.mesh-app.local:80:127.0.0.1 http://frontend.mesh-app.local/
### HTTPS
    curl -vk --resolve frontend.mesh-app.local:443:127.0.0.1 https://frontend.mesh-app.local/