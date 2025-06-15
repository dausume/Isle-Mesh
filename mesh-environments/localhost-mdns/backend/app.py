# app.py
import falcon

class StaticPageResource:
    def on_get(self, req, resp):
        html_content = """
        <!DOCTYPE html>
        <html lang="en">
            <head>
                <meta charset="UTF-8" />
                <title>Mesh App - Backend</title>
                <style>
                    body {
                    font-family: monospace;
                    background: #f1f8e9;
                    color: #33691e;
                    text-align: center;
                    padding: 3rem;
                    }
                    h1 {
                    font-size: 2.2rem;
                    }
                </style>
            </head>
            <body>
                <h1>Backend Service</h1>
                <p>You are viewing the <strong>backend.mesh-app.local</strong> endpoint.</p>
                <p>This is a placeholder for an API or backend dashboard.</p>
                <p>This is for convenience so you can configure nginx to enable your backend to be accessible externally or leave it to only be accessed internally.</p>
            </body>
        </html>
        """
        resp.status = falcon.HTTP_200
        resp.content_type = 'text/html'
        resp.text = html_content

app = falcon.App()
app.add_route('/', StaticPageResource())