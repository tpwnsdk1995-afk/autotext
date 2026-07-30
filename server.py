import base64
import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

PORT = 8000
STATIC_DIR = Path(__file__).parent
REQUEST_TIMEOUT = 15


def relay_get(url):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.status, resp.read()


def relay_post_json(url, username, password, payload):
    body = json.dumps(payload).encode("utf-8")
    auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth}",
        },
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.status, resp.read()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/health":
            qs = parse_qs(parsed.query)
            ip = (qs.get("ip") or [""])[0].strip()
            port = (qs.get("port") or [""])[0].strip()
            if not ip or not port:
                self._send_json(400, {"ok": False, "error": "ip/port가 필요합니다"})
                return
            url = f"http://{ip}:{port}/health"
            try:
                status, data = relay_get(url)
                self._send_json(200, {"ok": True, "status": status, "body": data.decode("utf-8", "replace")})
            except urllib.error.HTTPError as e:
                self._send_json(200, {"ok": False, "error": f"HTTP {e.code}"})
            except Exception as e:
                self._send_json(200, {"ok": False, "error": str(e)})
            return

        if parsed.path in ("/", "/index.html"):
            self._serve_file("index.html", "text/html; charset=utf-8")
            return

        self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/send":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json(400, {"ok": False, "error": "잘못된 요청 본문"})
                return

            ip = str(data.get("ip", "")).strip()
            port = str(data.get("port", "")).strip()
            username = str(data.get("username", ""))
            password = str(data.get("password", ""))
            message = str(data.get("message", ""))
            numbers = data.get("numbers", [])

            if not ip or not port or not username or not numbers:
                self._send_json(400, {"ok": False, "error": "ip/port/username/numbers가 필요합니다"})
                return

            url = f"http://{ip}:{port}/message"
            payload = {"textMessage": {"text": message}, "phoneNumbers": numbers}
            try:
                status, body = relay_post_json(url, username, password, payload)
                self._send_json(200, {"ok": True, "status": status, "body": body.decode("utf-8", "replace")})
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace") if e.fp else ""
                self._send_json(200, {"ok": False, "status": e.code, "error": f"HTTP {e.code}", "detail": detail})
            except Exception as e:
                self._send_json(200, {"ok": False, "error": f"연결 실패: {e}"})
            return

        self.send_error(404)

    def _serve_file(self, name, content_type):
        path = STATIC_DIR / name
        if not path.exists():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"서버 실행 중: http://localhost:{PORT}  (종료하려면 Ctrl+C)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
