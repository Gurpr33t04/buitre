from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer


class RedirectHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/old.html":
            self.send_response(302)
            self.send_header(
                "Location",
                "/final.html",
            )
            self.end_headers()
            return

        if self.path == "/alias.html":
            self.send_response(301)
            self.send_header(
                "Location",
                "/final.html",
            )
            self.end_headers()
            return

        if self.path == "/final.html":
            body = b"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Final Redirect Destination</title>
            </head>
            <body>
                <h1>Final Redirect Destination</h1>
                <p>This page was reached after a redirect.</p>
            </body>
            </html>
            """

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/html; charset=utf-8",
            )
            self.send_header(
                "Content-Length",
                str(len(body)),
            )
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return


server = HTTPServer(
    ("127.0.0.1", 8766),
    RedirectHandler,
)

print(
    "Redirect test server running on "
    "http://127.0.0.1:8766"
)

server.serve_forever()
