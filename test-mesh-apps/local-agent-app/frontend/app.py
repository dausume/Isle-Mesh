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
            <h1>✅ Local Agent App Active</h1>
            <p>
            You are successfully serving the <strong>local-agent-app</strong> on <code>https://app.local-app.local</code> using mDNS and the unified isle-agent proxy.
            </p>
            <p>
            This confirms your isle-agent integration and self-hosting environment are running correctly.
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

# Serve on HTTP (isle-agent handles HTTPS)
if __name__ == '__main__':
    from wsgiref.simple_server import make_server
    with make_server('', 8080, app) as httpd:
        print("🚀 Frontend running on http://0.0.0.0:8080")
        httpd.serve_forever()