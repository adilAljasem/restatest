"""Minimal local Blend server for end-to-end sandbox verification."""

import argparse
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    log_path = None

    def _write_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self, body=None):
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "method": self.command,
            "path": self.path,
            "authorization": self.headers.get("Authorization", ""),
            "body": body,
        }
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._write_json(400, {"status": "failed"})
            return
        self._record(payload)
        if self.path == "/order-created":
            self._write_json(200, {"order_id": "mock-blend-order-900", "status": "success"})
        else:
            self._write_json(200, {"status": "success"})

    def do_GET(self):
        self._record()
        self._write_json(
            200,
            {
                "payload": {
                    "categories": [],
                    "items": [],
                    "toppings": [],
                    "branch_schedule_time": {},
                }
            },
        )

    def log_message(self, *args):
        return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8210)
    parser.add_argument("--log", required=True)
    args = parser.parse_args()
    Handler.log_path = Path(args.log).resolve()
    Handler.log_path.parent.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
