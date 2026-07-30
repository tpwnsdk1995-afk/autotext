import base64
import json
import re
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


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-5"
EXTRACT_TIMEOUT = 240

REPORT_PROMPT = """이 이미지는 렌탈 대여/반납 관리 표입니다. 표의 각 데이터 행을 읽어서, 아래 스키마의 JSON 배열만 출력하세요 (다른 설명이나 코드블록 없이 JSON만):

[{"type": "수령" 또는 "반납", "name": "수취인명", "phone": "010-1234-5678 형식", "reservedTime": "수령시간 열 값", "flight": "항공편코드 또는 null"}]

중요 — 정확성이 최우선입니다. 특히 phone(전화번호)과 전체 행 개수는 한 글자/한 행도 틀리면 안 됩니다:
- phone: 숫자 하나하나를 신중하게 확인하세요. 비슷하게 생긴 숫자(0/6/8, 1/7, 3/9 등)를 혼동하지 않도록 각 자리를 다시 확인한 뒤 적으세요. 절대 어림짐작하지 마세요.
- name: 실제로 인쇄된 글자를 최대한 정확히 읽으세요. 다만 "수령유형", "인천반납" 같은 열 이름/라벨 문구를 이름으로 쓰는 것은 절대 금지입니다 — 그건 실존 인물의 이름이 아닙니다.
- 행 개수: 이미지 안에서 온전히 다 보이는 데이터 행 하나당 정확히 JSON 항목 하나를 만드세요. 행을 빠뜨리거나 중복으로 만들지 마세요.
- 이미지 맨 위에 있는 일자/전체·수령·반납 건수 요약표(제목, 합계 숫자 칸)는 고객 데이터 행이 아니므로 절대 포함하지 마세요. 왼쪽에 NO(순번)가 매겨진 행만 데이터 행입니다.
- 이미지의 맨 위나 맨 아래 끝에서 행이 중간에 잘려 온전히 보이지 않으면(위쪽 절반만 보이거나 등), 그 행은 통째로 제외하세요 — 다른 이미지 조각에 온전히 나올 것입니다.

그 외 규칙:
- type: 왼쪽 "수령유형" 열이 "인천수령"이면 "수령", "인천반납"이면 "반납"
- flight: type이 "반납"인 행에서만 채우고, 그 외에는 null. "수령장소" 열 괄호 안에 항공편 코드(예: KE0086)가 있으면 그 값을 쓰고, 괄호가 없거나 비어있으면 "수령특이사항" 열에서 "입국편: XXXX" 형태로 적힌 항공편 코드를 사용. 그것도 없으면 null.
- 이름 옆 괄호 속 숫자(나이 등)는 name에 포함하지 말 것
"""


def call_anthropic_vision(api_key, image_b64, media_type, prompt):
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 8192,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=EXTRACT_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_json_array(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


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

        if parsed.path == "/api/flight-arrival":
            qs = parse_qs(parsed.query)
            service_key = (qs.get("serviceKey") or [""])[0]
            flight_id = (qs.get("flightId") or [""])[0].strip().upper()
            if not service_key or not flight_id:
                self._send_json(400, {"ok": False, "error": "serviceKey/flightId가 필요합니다"})
                return
            url = f"http://apis.data.go.kr/B551177/StatusOfPassengerFlightsDSOdp/getPassengerArrivalsDSOdp?serviceKey={service_key}&type=json"
            try:
                status, body = relay_get(url)
                data = json.loads(body.decode("utf-8"))
                items = (
                    data.get("response", {})
                    .get("body", {})
                    .get("items", {})
                    .get("item", [])
                )
                if isinstance(items, dict):
                    items = [items]
                norm_target = flight_id.replace(" ", "")

                def core(s):
                    m = re.match(r"([A-Z]+)0*(\d+)", s)
                    return (m.group(1), m.group(2)) if m else (s, "")

                target_core = core(norm_target)
                match = None
                for item in items:
                    fid = str(item.get("flightId", "")).replace(" ", "").upper()
                    if fid == norm_target or core(fid) == target_core:
                        match = item
                        break
                if match:
                    self._send_json(200, {"ok": True, "found": True, "item": match})
                else:
                    self._send_json(200, {"ok": True, "found": False})
            except Exception as e:
                self._send_json(200, {"ok": False, "error": str(e)})
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

        if parsed.path == "/api/extract-report":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json(400, {"ok": False, "error": "잘못된 요청 본문"})
                return

            api_key = str(data.get("apiKey", ""))
            image_b64 = str(data.get("imageBase64", ""))
            media_type = str(data.get("mediaType", "image/png"))

            if not api_key or not image_b64:
                self._send_json(400, {"ok": False, "error": "apiKey/imageBase64가 필요합니다"})
                return

            try:
                result = call_anthropic_vision(api_key, image_b64, media_type, REPORT_PROMPT)
                text = "".join(
                    block.get("text", "") for block in result.get("content", []) if block.get("type") == "text"
                )
                rows = extract_json_array(text)
                self._send_json(200, {"ok": True, "rows": rows})
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace") if e.fp else ""
                self._send_json(200, {"ok": False, "error": f"Claude API 오류 (HTTP {e.code})", "detail": detail})
            except json.JSONDecodeError as e:
                self._send_json(200, {"ok": False, "error": f"응답 파싱 실패: {e}"})
            except Exception as e:
                self._send_json(200, {"ok": False, "error": str(e)})
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
