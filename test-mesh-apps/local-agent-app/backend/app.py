# app.py
import falcon
import ssl
from wsgiref.simple_server import make_server, WSGIRequestHandler

class StaticPageResource:
    def on_get(self, req, resp):
        resp.status = falcon.HTTP_200
        resp.content_type = 'text/html'
        resp.text = """
        <!DOCTYPE html>
        <html>
        <head><title>Backend API</title></head>
        <body>
            <h1>Backend API Service</h1>
            <p>You are at <strong>api.local-app.local</strong></p>
            <p>This service uses mTLS for secure communication.</p>
        </body>
        </html>
        """

# Falcon app
app = falcon.App()
app.add_route("/", StaticPageResource())

# TLS context with mTLS (CERT_REQUIRED)
context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
context.load_cert_chain(certfile='/ssl/certs/api.local-app.local.crt',
                        keyfile='/ssl/keys/api.local-app.local.key')
context.load_verify_locations(cafile='/ssl/certs/local-app.local.crt')
context.verify_mode = ssl.CERT_REQUIRED  # Enforce mTLS

# Optional: override WSGIRequestHandler to suppress noisy logs
class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args): pass

# Serve app
if __name__ == '__main__':
    with make_server('', 8443, app, handler_class=QuietHandler) as httpd:
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        print("🚀 Backend with mTLS running on https://0.0.0.0:8443")
        httpd.serve_forever()