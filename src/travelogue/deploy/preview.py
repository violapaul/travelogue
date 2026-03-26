"""Local preview server for the generated static site."""

from __future__ import annotations

import http.server
import os
import socketserver
from pathlib import Path


def serve_site(publish_dir: Path, port: int = 8080) -> None:
    """Serve the static site at publish_dir on the given port."""
    os.chdir(publish_dir)

    handler = http.server.SimpleHTTPRequestHandler

    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"Serving at http://localhost:{port}  (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
