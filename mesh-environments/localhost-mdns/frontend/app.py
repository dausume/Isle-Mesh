# app.py
import falcon

class StaticPageResource:
    def on_get(self, req, resp):
        html_content = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
        <title>Mesh App: Localhost</title>
        <style>
            body {
            font-family: system-ui, sans-serif;
            background: #1e1e2f;
            color: #f4f4f4;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            }
            .container {
            text-align: center;
            background: #2a2a3c;
            padding: 2rem;
            border-radius: 10px;
            box-shadow: 0 0 30px rgba(0,0,0,0.3);
            }
            h1 {
            margin-bottom: 1rem;
            color: #00ffb2;
            }
            p {
            font-size: 1.1rem;
            max-width: 500px;
            margin: 0 auto;
            }
        </style>
        </head>
        <body>
        <div class="container">
            <h1>✅ Mesh App: Localhost Active</h1>
            <p>
            You are successfully serving the <strong>sample mesh-app</strong> on <code>https://frontend.mesh-app.local</code> using mDNS and secure localhost HTTPS.
            </p>
            <p>
            This confirms your proxy and self-hosting environment are running correctly.
            </p>
        </div>
        </body>
        </html>
        """
        resp.status = falcon.HTTP_200
        resp.content_type = 'text/html'
        resp.text = html_content

app = falcon.App()
app.add_route('/', StaticPageResource())