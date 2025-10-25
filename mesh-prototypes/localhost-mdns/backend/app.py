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
        <head><title>Backend</title></head>
        <body>
            <h1>Backend Service</h1>
            <p>You are at <strong>backend.mesh-app.local</strong></p>
        </body>
        </html>
        """

# Falcon app
app = falcon.App()
app.add_route("/", StaticPageResource())

# TLS context with mTLS (CERT_REQUIRED)
context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
context.load_cert_chain(certfile='/ssl/certs/backend.mesh-app.crt',
                        keyfile='/ssl/keys/backend.mesh-app.key')
context.load_verify_locations(cafile='/ssl/certs/mesh-app.crt')
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