import base64
import io
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, unquote


def format_hhmm(value):
    """'202608030025' 같은 YYYYMMDDHHmm 문자열을 'H:MM'으로 변환"""
    s = str(value or "")
    if len(s) < 12:
        return s
    h, m = int(s[8:10]), int(s[10:12])
    return f"{h}:{m:02d}"


def _flight_core(s):
    m = re.match(r"([A-Z]+)0*(\d+)", s)
    return (m.group(1), m.group(2)) if m else (s, "")


def normalize_service_key(key):
    """data.go.kr 포털은 서비스키를 이미 URL-인코딩된 형태(%2B 등)로 화면에 보여준다.
    사용자가 그 표시된 문자열을 그대로 복사-붙여넣기하면 원문에 '%XX'가 문자 그대로
    들어있게 되는데, 여기서 그대로 다시 quote()하면 이중 인코딩되어 키가 깨진다.
    이미 인코딩된 것으로 보이면 한 번 디코딩해서 '원본' 키로 되돌려 놓는다."""
    if re.search(r"%[0-9A-Fa-f]{2}", key):
        try:
            return unquote(key)
        except Exception:
            return key
    return key


def fetch_flight_items(service_key):
    """당일 기준 D+0~D+6 인천공항 도착 항공편 전체 목록을 한 번만 받아온다 (응답 2MB+)."""
    service_key = normalize_service_key(service_key)
    url = f"https://apis.data.go.kr/B551177/StatusOfPassengerFlightsDSOdp/getPassengerArrivalsDSOdp?serviceKey={quote(service_key, safe='')}&type=json"
    status, body = relay_get(url, timeout=60)
    data = json.loads(body.decode("utf-8"))
    items = data.get("response", {}).get("body", {}).get("items", [])
    if isinstance(items, dict):
        items = [items]
    return items


def match_flight(items, flight_id):
    """이미 받아온 items 목록에서 특정 편명의 당일 도착 정보를 찾는다."""
    norm_target = str(flight_id).replace(" ", "").upper()
    target_core = _flight_core(norm_target)
    today_str = datetime.now().strftime("%Y%m%d")

    candidates = [
        item for item in items
        if (lambda fid: fid == norm_target or _flight_core(fid) == target_core)(
            str(item.get("flightId", "")).replace(" ", "").upper()
        )
    ]

    today_match = next(
        (it for it in candidates if str(it.get("scheduleDateTime", "")).startswith(today_str)),
        None,
    )
    if today_match:
        item = dict(today_match)
        item["scheduleTime"] = format_hhmm(item.get("scheduleDateTime"))
        item["estimatedTime"] = format_hhmm(item.get("estimatedDateTime"))
        return {"found": True, "item": item}
    if candidates:
        return {"found": False, "error": "당일 도착편이 아닙니다"}
    return {"found": False}

PORT = 8000
STATIC_DIR = Path(__file__).parent
REQUEST_TIMEOUT = 15


def relay_get(url, timeout=REQUEST_TIMEOUT):
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
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

중요 — 정확성이 최우선입니다. 필드마다 요구되는 정확도가 다릅니다:

**아래 세 가지는 절대 틀리면 안 되는 최우선 항목입니다 (오차 허용 안 됨):**
- phone: 숫자 하나하나를 신중하게 확인하세요. 비슷하게 생긴 숫자(0/6/8, 1/7, 3/9 등)를 혼동하지 않도록 각 자리를 다시 확인한 뒤 적으세요. 절대 어림짐작하지 마세요.
- reservedTime: "수령시간" 열의 시:분 값을 정확히 옮기세요. 비슷한 시간대의 다른 행과 착각하지 않도록, 반드시 그 행 자체의 시간 칸만 보고 옮기세요. 시간이 틀리면 실제 업무에 큰 문제가 생깁니다.
- type: 왼쪽 "수령유형" 열 글자를 정확히 확인하세요 — "인천수령"이면 "수령", "인천반납"이면 "반납". 헷갈리면 셀 배경색(수령은 파란 계열, 반납은 분홍/빨간 계열)도 참고해서 재확인하세요.

**아래는 최대한 정확히 하되, 사소한 오차(비슷한 발음의 글자 하나 정도)는 완전한 실패는 아닙니다. 다만 사람 이름처럼 보이지 않을 정도로 크게 틀리면 안 됩니다:**
- name: 실제로 인쇄된 글자를 최대한 정확히 읽으세요. 다만 "수령유형", "인천반납" 같은 열 이름/라벨 문구를 이름으로 쓰는 것은 절대 금지입니다 — 그건 실존 인물의 이름이 아닙니다.

**행 개수 규칙:**
- 이미지 안에서 온전히 다 보이는 데이터 행 하나당 정확히 JSON 항목 하나를 만드세요. 행을 빠뜨리거나 중복으로 만들지 마세요.
- 이미지 맨 위에 있는 일자/전체·수령·반납 건수 요약표(제목, 합계 숫자 칸)는 고객 데이터 행이 아니므로 절대 포함하지 마세요. 왼쪽에 NO(순번)가 매겨진 행만 데이터 행입니다.
- 이미지의 맨 위나 맨 아래 끝에서 행이 중간에 잘려 온전히 보이지 않으면(위쪽 절반만 보이거나 등), 그 행은 통째로 제외하세요 — 다른 이미지 조각에 온전히 나올 것입니다.

모든 필드를 채우기 전에, 그 행을 다시 한 번 눈으로 확인하는 셈 치고 phone/reservedTime/type 세 값을 재검토한 뒤 출력하세요.

그 외 규칙:
- flight: type이 "반납"인 행에서만 채우고, 그 외에는 null. "수령장소" 열 괄호 안에 항공편 코드(예: KE0086)가 있으면 그 값을 쓰고, 괄호가 없거나 비어있으면 "수령특이사항" 열에서 "입국편: XXXX" 형태로 적힌 항공편 코드를 사용. 그것도 없으면 null.
- 이름 옆 괄호 속 숫자(나이 등)는 name에 포함하지 말 것
"""

COUNT_PROMPT = """이 이미지는 렌탈 대여/반납 관리 표입니다. 이미지 맨 위의 일자/전체·수령·반납 합계 요약표(제목, 합계 숫자 칸)는 제외하고, 왼쪽에 NO(순번)가 매겨진 실제 고객 데이터 행이 총 몇 개인지 처음부터 끝까지 세세요. 숫자만 출력하세요 (설명, 단위 없이 정수만, 예: 85)."""


# ---------- 엑셀(xlsx) 원본 파일 직접 읽기 (AI 없이, 100% 정확) ----------
XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
PREFERRED_SHEET_NAMES = ["2터미널", "전체일정", "1터미널"]
FLIGHT_PAREN_RE = re.compile(r"\(([A-Za-z0-9]+)\)")
FLIGHT_ARRIVAL_RE = re.compile(r"입국편\s*[:：]\s*([A-Za-z0-9]+)")


def col_letters(cell_ref):
    return "".join(ch for ch in cell_ref if ch.isalpha())


def load_shared_strings(zf):
    shared = []
    try:
        with zf.open("xl/sharedStrings.xml") as f:
            for _event, elem in ET.iterparse(f, events=("end",)):
                if elem.tag == XLSX_NS + "si":
                    text = "".join(t.text or "" for t in elem.iter() if t.tag == XLSX_NS + "t")
                    shared.append(text)
                    elem.clear()
    except KeyError:
        pass
    return shared


def pick_sheet_file(zf):
    workbook_xml = zf.read("xl/workbook.xml").decode("utf-8")
    rels_xml = zf.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    sheets = re.findall(r'<sheet name="([^"]+)"[^>]*r:id="(rId\d+)"', workbook_xml)
    rels = dict(re.findall(r'<Relationship Id="(rId\d+)"[^>]*Target="worksheets/(sheet\d+\.xml)"', rels_xml))
    name_to_file = {name: rels[rid] for name, rid in sheets if rid in rels}
    for preferred in PREFERRED_SHEET_NAMES:
        if preferred in name_to_file:
            return name_to_file[preferred], preferred
    if sheets:
        name, rid = sheets[0]
        return rels.get(rid), name
    return None, None


def excel_time_to_hhmm(value):
    try:
        frac = float(value)
    except (TypeError, ValueError):
        return ""
    total_minutes = round((frac % 1) * 24 * 60)
    h, m = divmod(total_minutes, 60)
    return f"{h}:{m:02d}"


def extract_flight(location, note):
    if location:
        m = FLIGHT_PAREN_RE.search(location)
        if m:
            return m.group(1)
    if note:
        m = FLIGHT_ARRIVAL_RE.search(note)
        if m:
            return m.group(1)
    return None


def parse_rental_sheet(file_bytes):
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
        shared = load_shared_strings(zf)
        sheet_file, sheet_name = pick_sheet_file(zf)
        if not sheet_file:
            raise ValueError("시트를 찾을 수 없습니다")

        rows_out = []
        header_seen = False
        with zf.open(f"xl/worksheets/{sheet_file}") as f:
            for _event, elem in ET.iterparse(f, events=("end",)):
                if elem.tag != XLSX_NS + "row":
                    continue
                cells = {}
                for c in elem:
                    ref = c.get("r")
                    if not ref:
                        continue
                    col = col_letters(ref)
                    t = c.get("t")
                    v = c.find(XLSX_NS + "v")
                    val = v.text if v is not None else None
                    if t == "s" and val is not None:
                        idx = int(val)
                        val = shared[idx] if idx < len(shared) else ""
                    cells[col] = val
                elem.clear()

                no_val = cells.get("A")
                type_val = cells.get("B")
                name_val = cells.get("E")
                phone_val = cells.get("F")

                if type_val == "수령유형":
                    header_seen = True
                    continue
                if not header_seen:
                    continue
                if not no_val or not str(no_val).strip().isdigit():
                    continue
                if not name_val or not phone_val:
                    continue

                row_type = "반납" if type_val and "반납" in type_val else "수령"
                flight = extract_flight(cells.get("G"), cells.get("J")) if row_type == "반납" else None

                rows_out.append({
                    "type": row_type,
                    "name": str(name_val).strip(),
                    "phone": str(phone_val).strip(),
                    "reservedTime": excel_time_to_hhmm(cells.get("H")),
                    "flight": flight,
                })
        return rows_out, sheet_name


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
            try:
                items = fetch_flight_items(service_key)
                result = match_flight(items, flight_id)
                self._send_json(200, {"ok": True, **result})
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

        if parsed.path == "/api/count-rows":
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
                result = call_anthropic_vision(api_key, image_b64, media_type, COUNT_PROMPT)
                text = "".join(
                    block.get("text", "") for block in result.get("content", []) if block.get("type") == "text"
                )
                match = re.search(r"\d+", text)
                if not match:
                    self._send_json(200, {"ok": False, "error": f"숫자 응답을 못 받았습니다: {text[:100]}"})
                    return
                self._send_json(200, {"ok": True, "count": int(match.group())})
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace") if e.fp else ""
                self._send_json(200, {"ok": False, "error": f"Claude API 오류 (HTTP {e.code})", "detail": detail})
            except Exception as e:
                self._send_json(200, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/parse-xlsx":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json(400, {"ok": False, "error": "잘못된 요청 본문"})
                return

            file_b64 = str(data.get("fileBase64", ""))
            if not file_b64:
                self._send_json(400, {"ok": False, "error": "fileBase64가 필요합니다"})
                return

            try:
                file_bytes = base64.b64decode(file_b64)
                rows, sheet_name = parse_rental_sheet(file_bytes)
                self._send_json(200, {"ok": True, "rows": rows, "sheet": sheet_name})
            except Exception as e:
                self._send_json(200, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/flight-arrivals-batch":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json(400, {"ok": False, "error": "잘못된 요청 본문"})
                return

            service_key = str(data.get("serviceKey", ""))
            flight_ids = data.get("flightIds", [])
            if not service_key or not flight_ids:
                self._send_json(400, {"ok": False, "error": "serviceKey/flightIds가 필요합니다"})
                return

            try:
                items = fetch_flight_items(service_key)  # 전체 목록 한 번만 조회
                results = {fid: match_flight(items, fid) for fid in flight_ids}
                self._send_json(200, {"ok": True, "results": results})
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
