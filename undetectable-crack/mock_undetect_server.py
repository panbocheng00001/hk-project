#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 mock：模拟 Undetectable 的 REST 行为，用于联调 create20.py（不依赖真实客户端）"""
import json, re, sys, threading, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CREATED = []
LOG = []

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/status":
            return self._send({"status": "ok", "version": "1.0.0"})
        if p == "/timezoneslist":
            return self._send(["America/New_York", "Europe/Berlin", "Asia/Tokyo"])
        if p == "/configslist":
            return self._send([{"id": -1, "name": "default"}])
        if p == "/list":
            return self._send([{"id": c["id"], "name": c["name"]} for c in CREATED])
        m = re.match(r"^/profile/start/(.+)$", p)
        if m:
            pid = m.group(1)
            hit = [c for c in CREATED if c["id"] == pid]
            if not hit:
                return self._send({"error": "Invalid profile id!"})
            hit[0]["started"] = True
            return self._send({"state": "ok", "id": pid})
        return self._send({"error": "Not Found"}, 404)

    def do_POST(self):
        p = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            return self._send({"error": "Invalid profile id!"})
        LOG.append(body)
        if p == "/profile/create":
            if not body.get("name"):
                return self._send({"error": "Name is empty!"})
            if not body.get("os"):
                return self._send({"error": "unable to get config!"})
            pid = uuid.uuid4().hex[:16]
            CREATED.append({"id": pid, "name": body["name"], "body": body})
            return self._send({"profile_id": pid})
        return self._send({"error": "Not Found"}, 404)

def serve(port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.serve_forever()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    serve(port)
