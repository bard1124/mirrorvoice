"""A tiny WebSocket server speaking the 阿里云百炼 Fun-ASR realtime protocol, for tests.

script: list of (after_ms, kind, text, begin_ms, end_ms) per task — sent once the task has
received after_ms of audio; kind "partial" | "final". Records every run-task, finish-task and the
audio bytes per task. key "bad" fails the handshake with 401; fail_after_ms sends task-failed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _frame(payload: bytes, opcode: int) -> bytes:
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + payload


def _read_exact(conn, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = conn.recv(n - len(out))
        if not chunk:
            raise ConnectionError
        out += chunk
    return out


def _read_frame(conn) -> tuple[int, bytes]:
    b1, b2 = _read_exact(conn, 2)
    opcode, n = b1 & 0x0F, b2 & 0x7F
    if n == 126:
        n = struct.unpack(">H", _read_exact(conn, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", _read_exact(conn, 8))[0]
    mask = _read_exact(conn, 4) if b2 & 0x80 else b""
    data = _read_exact(conn, n)
    if mask:
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
    return opcode, data


class FakeDashScope:
    def __init__(self, script=None, fail_after_ms: int | None = None) -> None:
        self.script = list(script or [])
        self.fail_after_ms = fail_after_ms
        self.tasks: list[dict] = []        # run-task payloads
        self.finished: list[str] = []      # task ids
        self.audio: dict[str, bytearray] = {}
        self.headers: list[str] = []
        self.connections = 0
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.url = f"ws://127.0.0.1:{self.sock.getsockname()[1]}/api-ws/v1/inference"
        threading.Thread(target=self._serve, daemon=True).start()

    def close(self) -> None:
        self.sock.close()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn) -> None:
        req = b""
        while b"\r\n\r\n" not in req:
            req += conn.recv(4096)
        text = req.decode("latin-1")
        self.headers.append(text)
        if "Bearer bad" in text:
            conn.sendall(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")
            conn.close()
            return
        key = next(l.split(":", 1)[1].strip() for l in text.split("\r\n") if l.lower().startswith("sec-websocket-key"))
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        self.connections += 1
        task, sent = "", 0
        pending: list = []
        send = lambda d: conn.sendall(_frame(json.dumps(d).encode(), 1))  # noqa: E731
        try:
            while True:
                op, data = _read_frame(conn)
                if op == 8:
                    conn.close()
                    return
                if op == 1:
                    d = json.loads(data)
                    action, tid = d["header"]["action"], d["header"]["task_id"]
                    if action == "run-task":
                        task, sent = tid, 0
                        self.tasks.append(d["payload"])
                        self.audio[task] = bytearray()
                        pending = list(self.script)
                        send({"header": {"event": "task-started", "task_id": tid}, "payload": {}})
                    elif action == "finish-task":
                        self.finished.append(tid)
                        for _after, kind, txt, b, e in pending:
                            if kind == "final":
                                self._result(send, tid, txt, b, e, True)
                        pending = []
                        send({"header": {"event": "task-finished", "task_id": tid}, "payload": {}})
                elif op == 2 and task:
                    self.audio[task] += data
                    sent = len(self.audio[task]) // 32  # 16 kHz * 2 bytes = 32 bytes per ms
                    if self.fail_after_ms is not None and sent >= self.fail_after_ms:
                        send({"header": {"event": "task-failed", "task_id": task, "error_code": "ServerError",
                                         "error_message": "boom"}, "payload": {}})
                        conn.close()
                        return
                    while pending and sent >= pending[0][0]:
                        _after, kind, txt, b, e = pending.pop(0)
                        self._result(send, task, txt, b, e, kind == "final")
        except (ConnectionError, OSError, ValueError):
            conn.close()

    @staticmethod
    def _result(send, tid, text, b, e, final) -> None:
        send({"header": {"event": "result-generated", "task_id": tid},
              "payload": {"output": {"sentence": {"begin_time": b, "end_time": e, "text": text,
                                                  "sentence_end": final, "heartbeat": False}}}})
