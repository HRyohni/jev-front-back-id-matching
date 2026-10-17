"""Tiny local receiver: the browser POSTs images to http://127.0.0.1:<port>/<name>.jpg,
this script writes them into <out_dir>. Used by prado_download.js.

    python tools/receive.py data_prado 18765
"""
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

OUT = Path(sys.argv[1])
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 18765
OUT.mkdir(parents=True, exist_ok=True)


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        name = self.path.strip("/")
        if not re.fullmatch(r"[A-Za-z0-9_\-]+\.jpg", name):
            self.send_response(400)
            self._cors()
            self.end_headers()
            return
        data = self.rfile.read(int(self.headers["Content-Length"]))
        (OUT / name).write_bytes(data)
        print("saved", name, len(data), "bytes", flush=True)
        self.send_response(200)
        self._cors()
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


print(f"listening on http://127.0.0.1:{PORT} -> {OUT}")
HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
