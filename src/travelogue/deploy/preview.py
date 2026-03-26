"""Local preview server for the generated static site."""

from __future__ import annotations

import http.server
import os
import socketserver
from pathlib import Path
from functools import partial
from urllib.parse import unquote


class _ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def serve_site(publish_dir: Path, port: int = 8080) -> None:
    """Serve the static site at publish_dir on the given port."""
    os.chdir(publish_dir)

    handler = http.server.SimpleHTTPRequestHandler

    with _ReusableTCPServer(("", port), handler) as httpd:
        print(f"Serving at http://localhost:{port}  (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


class _MultiTripHandler(http.server.SimpleHTTPRequestHandler):
    """Serves multiple trip publish dirs under /<trip-id>/ prefixes."""

    trip_dirs: dict[str, Path] = {}

    def translate_path(self, path: str) -> str:
        path = unquote(path)
        parts = path.strip("/").split("/", 1)
        trip_id = parts[0] if parts else ""
        remainder = parts[1] if len(parts) > 1 else ""

        if trip_id in self.trip_dirs:
            return str(self.trip_dirs[trip_id] / remainder)
        return str(Path(self.trip_dirs.get("", Path.cwd())) / path.lstrip("/"))

    def do_GET(self) -> None:
        path = unquote(self.path)
        stripped = path.strip("/")

        if stripped == "" or stripped not in self.trip_dirs and "/" not in stripped:
            self._serve_index()
            return

        if stripped in self.trip_dirs and not path.endswith("/"):
            self.send_response(301)
            self.send_header("Location", f"/{stripped}/")
            self.end_headers()
            return

        super().do_GET()

    def _serve_index(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        links = "".join(
            f'<li style="margin:0.5em 0"><a href="/{tid}/">{tid}</a></li>'
            for tid in sorted(self.trip_dirs)
        )
        html = (
            "<!doctype html><html><head><title>Travelogue</title>"
            "<style>body{font-family:system-ui;max-width:600px;margin:3em auto;}</style>"
            "</head><body>"
            f"<h1>Travelogues</h1><ul>{links}</ul>"
            "</body></html>"
        )
        self.wfile.write(html.encode())


def serve_multi(trips_root: Path, trip_ids: list[str], port: int = 8080) -> None:
    """Serve multiple trips under /<trip-id>/ from one server."""
    trip_dirs: dict[str, Path] = {}
    for tid in trip_ids:
        pub = trips_root / tid / "publish"
        if pub.is_dir() and any(pub.iterdir()):
            trip_dirs[tid] = pub

    if not trip_dirs:
        print("No published trips found.")
        return

    handler = type("Handler", (_MultiTripHandler,), {"trip_dirs": trip_dirs})

    with _ReusableTCPServer(("", port), handler) as httpd:
        print(f"Serving {len(trip_dirs)} trip(s) at http://localhost:{port}")
        for tid in sorted(trip_dirs):
            print(f"  http://localhost:{port}/{tid}/")
        print("(Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
