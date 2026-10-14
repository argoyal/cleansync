"""A tiny pretend unsubscribe web site, for the Unsubscribe browser test only.

    python -m app.devtools.fake_unsub_site --port 8766

POST /one-click answers 200 "ok" and records the hit; GET /hits lists the hits as JSON.
Like the pretend accounts, it refuses to run without DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1."""

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeUnsubSite(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, _Handler)
        self.hits: list[dict] = []
        self.lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    server: FakeUnsubSite

    def _send(self, status: int, body: bytes, kind: str = "text/plain; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        if self.path != "/one-click":
            return self._send(404, b"not found")
        with self.server.lock:
            self.server.hits.append({"method": "POST", "path": self.path, "body": body})
        self._send(200, b"ok")

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/hits":
            return self._send(404, b"not found")
        with self.server.lock:
            data = json.dumps(self.server.hits).encode()
        self._send(200, data, "application/json")

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


def allowed() -> bool:
    return os.environ.get("DRIVESYNC_ALLOW_LOCAL_ACCOUNTS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.devtools.fake_unsub_site")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)
    if not allowed():
        print(
            "Refusing: the pretend unsubscribe site is for development and tests only. "
            "Set DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1 to use it.",
            file=sys.stderr,
        )
        return 2
    FakeUnsubSite(("127.0.0.1", args.port)).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
