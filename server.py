#!/usr/bin/env python3
import asyncio
import json
import os
import random
import socket
import string
import subprocess
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import websockets

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
HTTP_PORT = 8080
WS_PORT = 8081


def generate_token(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def detect_lan_ip() -> str:
    candidates: list[str] = []
    hostname = socket.gethostname()
    try:
        for item in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = item[4][0]
            if ip.startswith(("10.", "172.", "192.168.")):
                candidates.append(ip)
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip.startswith(("10.", "172.", "192.168.")):
                candidates.append(ip)
    except OSError:
        pass

    for ip in sorted(set(candidates)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((ip, 0))
                return ip
        except OSError:
            continue

    raise RuntimeError("Keine LAN-IP erkannt. Bitte mit aktivem WLAN/LAN starten.")


class AppState:
    def __init__(self, token: str):
        self.token = token
        self.lock = threading.Lock()
        self.clients: dict[Any, dict[str, Any]] = {}
        self.teacher_id: str | None = None
        self.rooms: dict[str, dict[str, Any]] = {}
        self.room_counter = 0

    def participant_info(self) -> dict[str, int]:
        with self.lock:
            total = len(self.clients)
            students = sum(1 for c in self.clients.values() if c["role"] == "student")
        return {"total": total, "students": students}

    def room_summaries(self) -> list[dict[str, Any]]:
        with self.lock:
            return [
                {
                    "room": room_id,
                    "filename": room["filename"],
                    "current_page": room["current_page"],
                }
                for room_id, room in self.rooms.items()
            ]


TOKEN = generate_token()
STATE = AppState(TOKEN)


def random_student_color() -> str:
    return random.choice(["#1d4ed8", "#16a34a", "#7e22ce", "#ea580c", "#0f766e", "#be123c"])


async def broadcast(message: dict[str, Any]) -> None:
    payload = json.dumps(message)
    targets = list(STATE.clients.keys())
    if not targets:
        return
    await asyncio.gather(*(ws.send(payload) for ws in targets), return_exceptions=True)


async def send_to(ws: Any, message: dict[str, Any]) -> None:
    await ws.send(json.dumps(message))


def ensure_token(query: dict[str, list[str]]) -> bool:
    return query.get("token", [""])[0] == STATE.token


def parse_points(raw_points: Any) -> list[list[float]]:
    points: list[list[float]] = []
    if not isinstance(raw_points, list):
        return points
    for point in raw_points:
        if not isinstance(point, list) or len(point) < 3:
            continue
        try:
            x = float(point[0])
            y = float(point[1])
            p = float(point[2])
        except (TypeError, ValueError):
            continue
        points.append([min(1.0, max(0.0, x)), min(1.0, max(0.0, y)), min(1.0, max(0.0, p))])
    return points


def parse_page(value: Any, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, parsed)


class AppHTTPRequestHandler(BaseHTTPRequestHandler):
    def _deny(self, message: str = "Ungültiger Token") -> None:
        body = message.encode("utf-8")
        self.send_response(HTTPStatus.FORBIDDEN)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if not ensure_token(query):
            self._deny()
            return

        if parsed.path in ("/", "/index.html"):
            self._send_bytes((STATIC_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            return

        if parsed.path.startswith("/static/"):
            rel = parsed.path.removeprefix("/static/")
            fs_path = (STATIC_DIR / rel).resolve()
            if not str(fs_path).startswith(str(STATIC_DIR.resolve())) or not fs_path.exists():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            mime = "application/octet-stream"
            if fs_path.suffix == ".js":
                mime = "application/javascript; charset=utf-8"
            elif fs_path.suffix == ".css":
                mime = "text/css; charset=utf-8"
            elif fs_path.suffix == ".html":
                mime = "text/html; charset=utf-8"
            self._send_bytes(fs_path.read_bytes(), mime)
            return

        if parsed.path == "/api/state":
            payload = {"rooms": STATE.room_summaries(), "participants": STATE.participant_info()}
            self._send_bytes(json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
            return

        if parsed.path.startswith("/api/pdf/"):
            room_id = parsed.path.split("/")[-1]
            with STATE.lock:
                room = STATE.rooms.get(room_id)
                if not room or not room["pdf_bytes"]:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                data = room["pdf_bytes"]
            self._send_bytes(data, "application/pdf")
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if not ensure_token(query):
            self._deny()
            return

        if parsed.path != "/api/upload":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        user_id = self.headers.get("X-User-Id", "")
        with STATE.lock:
            client = next((c for c in STATE.clients.values() if c["id"] == user_id), None)
        if not client or client["role"] != "teacher":
            self.send_error(HTTPStatus.FORBIDDEN, "Nur Lehrer darf PDF hochladen")
            return

        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype or "boundary=" not in ctype:
            self.send_error(HTTPStatus.BAD_REQUEST, "multipart/form-data erwartet")
            return

        boundary = ctype.split("boundary=", 1)[1].encode("utf-8")
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)

        file_bytes = b""
        filename = "dokument.pdf"
        for part in raw.split(b"--" + boundary):
            if b'Content-Disposition: form-data; name="file"' not in part:
                continue
            headers, _, body = part.partition(b"\r\n\r\n")
            if b"filename=" in headers:
                disp = headers.decode("utf-8", errors="ignore")
                for segment in disp.split(";"):
                    segment = segment.strip()
                    if segment.startswith("filename="):
                        filename = segment.split("=", 1)[1].strip('"')
                        break
            file_bytes = body.rstrip(b"\r\n")
            break

        if not file_bytes.startswith(b"%PDF"):
            self.send_error(HTTPStatus.BAD_REQUEST, "Nur PDF-Dateien erlaubt")
            return

        with STATE.lock:
            STATE.room_counter += 1
            room_id = f"pdf{STATE.room_counter}"
            STATE.rooms[room_id] = {
                "room": room_id,
                "filename": os.path.basename(filename) or f"{room_id}.pdf",
                "pdf_bytes": file_bytes,
                "current_page": 1,
                "strokes": {},
            }

        self._send_bytes(json.dumps({"ok": True, "room": room_id}).encode("utf-8"), "application/json; charset=utf-8")
        asyncio.run_coroutine_threadsafe(
            broadcast(
                {
                    "type": "pdf_upload",
                    "room": room_id,
                    "filename": os.path.basename(filename),
                    "pdf_url": f"/api/pdf/{room_id}?token={STATE.token}",
                    "page": 1,
                }
            ),
            WS_LOOP,
        )

    def log_message(self, *_args: Any) -> None:
        return


async def ws_handler(ws: Any) -> None:
    parsed = urllib.parse.urlparse(ws.request.path)
    query = urllib.parse.parse_qs(parsed.query)
    if not ensure_token(query):
        await ws.close(code=4003, reason="forbidden")
        return

    requested_role = query.get("role", ["student"])[0]
    with STATE.lock:
        role = "teacher" if requested_role == "teacher" and STATE.teacher_id is None else "student"
        client_id = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
        color = "#ff0000" if role == "teacher" else random_student_color()
        STATE.clients[ws] = {"id": client_id, "role": role, "color": color}
        if role == "teacher":
            STATE.teacher_id = client_id
        room_payload = [
            {
                "room": room_id,
                "filename": room["filename"],
                "pdf_url": f"/api/pdf/{room_id}?token={STATE.token}",
                "current_page": room["current_page"],
                "strokes": room["strokes"],
            }
            for room_id, room in STATE.rooms.items()
        ]

    await send_to(
        ws,
        {
            "type": "welcome",
            "user_id": client_id,
            "role": role,
            "color": color,
            "rooms": room_payload,
            "participants": STATE.participant_info(),
        },
    )
    await broadcast({"type": "participants", **STATE.participant_info()})

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type")
            room_id = msg.get("room", "")
            page = parse_page(msg.get("page", 1), default=1)

            with STATE.lock:
                me = STATE.clients.get(ws)
            if not me:
                continue

            if mtype == "stroke":
                points = parse_points(msg.get("points"))
                if not room_id or not points:
                    continue
                erase = bool(msg.get("erase", False))
                stroke_id = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(10))
                stroke = {
                    "id": stroke_id,
                    "type": "stroke",
                    "room": room_id,
                    "page": page,
                    "points": points,
                    "color": me["color"] if not erase else "#000000",
                    "width": min(6.0, max(1.0, float(msg.get("width", 2)))),
                    "erase": erase,
                    "user_id": me["id"],
                }
                with STATE.lock:
                    room = STATE.rooms.get(room_id)
                    if not room:
                        continue
                    room["strokes"].setdefault(str(page), []).append(stroke)
                await broadcast(stroke)

            elif mtype == "page_change" and room_id:
                with STATE.lock:
                    room = STATE.rooms.get(room_id)
                    if not room:
                        continue
                    room["current_page"] = page
                await broadcast({"type": "page_change", "room": room_id, "page": page})

            elif mtype == "undo" and room_id:
                removed_id = None
                with STATE.lock:
                    room = STATE.rooms.get(room_id)
                    if not room:
                        continue
                    strokes = room["strokes"].get(str(page), [])
                    for idx in range(len(strokes) - 1, -1, -1):
                        if strokes[idx].get("user_id") == me["id"]:
                            removed_id = strokes[idx].get("id")
                            strokes.pop(idx)
                            break
                if removed_id:
                    await broadcast({"type": "undo", "room": room_id, "page": page, "stroke_id": removed_id})

            elif mtype == "clear" and room_id and me["role"] == "teacher":
                with STATE.lock:
                    room = STATE.rooms.get(room_id)
                    if not room:
                        continue
                    room["strokes"][str(page)] = []
                await broadcast({"type": "clear", "room": room_id, "page": page})

    finally:
        with STATE.lock:
            gone = STATE.clients.pop(ws, None)
            if gone and gone["id"] == STATE.teacher_id:
                STATE.teacher_id = None
        await broadcast({"type": "participants", **STATE.participant_info()})


WS_LOOP: asyncio.AbstractEventLoop


def run_http_server(host: str) -> None:
    ThreadingHTTPServer((host, HTTP_PORT), AppHTTPRequestHandler).serve_forever()


def print_qr_code(url: str) -> None:
    print("📱 QR-Code:")
    try:
        output = subprocess.run(["qrencode", "-t", "ANSIUTF8", url], check=True, capture_output=True, text=True)
        print(output.stdout)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print(url)


async def main() -> None:
    global WS_LOOP
    host = detect_lan_ip()
    WS_LOOP = asyncio.get_running_loop()

    threading.Thread(target=run_http_server, args=(host,), daemon=True).start()

    teacher_url = f"http://{host}:{HTTP_PORT}/?token={STATE.token}&role=teacher"
    student_url = f"http://{host}:{HTTP_PORT}/?token={STATE.token}"

    print(f"✅ Server läuft auf http://{host}:{HTTP_PORT}")
    print(f"🔑 Token: {STATE.token}")
    print(f"👨‍🏫 Lehrer-URL: {teacher_url}")
    print_qr_code(student_url)

    async with websockets.serve(ws_handler, host, WS_PORT, max_size=4 * 1024 * 1024):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
