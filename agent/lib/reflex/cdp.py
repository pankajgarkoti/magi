"""cdp: CDP over websocket to the agent Chrome on 127.0.0.1:9222 — pure stdlib.

This is the browser-bus.mjs connection pattern ported to python (rewriting is fine,
new concepts are not): resolve the endpoint via /json/version on the fixed debug port,
speak one websocket to the browser target, send commands with incrementing ids, and
dispatch events to handlers. magi-reflex scripts talk CDP exactly like browser-bus does.

No third-party modules: the websocket client below is ~120 lines of RFC 6455 on top of
`socket` (client frames masked, server frames unmasked, ping/pong, continuation frames).
"""
import base64
import http.client
import json
import os
import socket
import threading
import time

DEFAULT_PORT = 9222
WS_MAX_FRAME = 64 * 1024 * 1024


class CDPError(Exception):
    pass


class CDPTimeout(CDPError):
    pass


class CDPClosed(CDPError):
    pass


class _Ws:
    """Minimal RFC 6455 websocket client (text frames only, which is all CDP sends)."""

    def __init__(self, host, port, path, timeout=10.0):
        self._buf = b""
        self._closed = threading.Event()
        self._lock = threading.Lock()  # serializes frame writes
        self.sock = socket.create_connection((host, port), timeout=timeout)
        try:
            self.sock.settimeout(None)
            key = base64.b64encode(os.urandom(16)).decode()
            req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            )
            self.sock.sendall(req.encode())
            self.sock.settimeout(timeout)
            while b"\r\n\r\n" not in self._buf:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise CDPError("websocket closed during handshake")
                self._buf += chunk
            head, self._buf = self._buf.split(b"\r\n\r\n", 1)
            if b" 101 " not in head.split(b"\r\n", 1)[0]:
                raise CDPError(f"websocket handshake refused: {head.split(b'\r\n')[0].decode(errors='replace')}")
            self.sock.settimeout(None)
        except Exception:
            try:
                self.sock.close()
            except OSError:
                pass
            raise

    def send_text(self, text):
        if self._closed.is_set():
            raise CDPClosed("websocket closed")
        payload = text.encode()
        mask = os.urandom(4)
        header = bytearray([0x81])  # FIN + text opcode
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += n.to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += n.to_bytes(8, "big")
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._lock:
            self.sock.sendall(bytes(header) + masked)

    def _read_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CDPClosed("websocket closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv_text(self, timeout=None):
        """Block until one complete text message (joining continuation frames)."""
        if self._closed.is_set():
            raise CDPClosed("websocket closed")
        if timeout is not None:
            self.sock.settimeout(timeout)
        try:
            while True:
                h = self._read_exact(2)
                fin, opcode = bool(h[0] & 0x80), h[0] & 0x0F
                masked, n = bool(h[1] & 0x80), h[1] & 0x7F
                if n == 126:
                    n = int.from_bytes(self._read_exact(2), "big")
                elif n == 127:
                    n = int.from_bytes(self._read_exact(8), "big")
                if n > WS_MAX_FRAME:
                    raise CDPError(f"websocket frame too large ({n})")
                mask = self._read_exact(4) if masked else None
                data = self._read_exact(n)
                if mask:
                    data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
                if opcode == 0x8:  # close
                    self._closed.set()
                    try:
                        self.sock.close()
                    except OSError:
                        pass
                    raise CDPClosed("websocket closed by peer")
                if opcode == 0x9:  # ping -> pong (masked, same payload)
                    pong = bytearray([0x8A])
                    pn = len(data)
                    pmask = os.urandom(4)
                    pong.append(0x80 | (pn if pn < 126 else 126))
                    if pn >= 126:
                        pong += pn.to_bytes(2, "big")
                    pong += pmask
                    pong += bytes(b ^ pmask[i % 4] for i, b in enumerate(data))
                    with self._lock:
                        try:
                            self.sock.sendall(bytes(pong))
                        except OSError:
                            pass
                    continue
                if opcode == 0xA:  # pong
                    continue
                if opcode in (0x1, 0x0):
                    if opcode == 0x1:  # start of a message; require FIN on the last part
                        msg = data
                    else:
                        msg += data
                    if fin:
                        return msg.decode("utf-8", "replace")
        finally:
            if timeout is not None:
                self.sock.settimeout(None)

    def close(self):
        self._closed.set()
        try:
            self.sock.close()
        except OSError:
            pass


def browser_ws_url(port=DEFAULT_PORT, timeout=3.0):
    """The browser-level webSocketDebuggerUrl from /json/version (browser-bus 'port' mode)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", "/json/version")
        r = conn.getresponse()
        if r.status != 200:
            raise CDPError(f"no browser on 127.0.0.1:{port} ({r.status}); start it with chrome-debug")
        return json.loads(r.read().decode())["webSocketDebuggerUrl"]
    except (OSError, json.JSONDecodeError, KeyError) as e:
        raise CDPError(f"no browser debugging endpoint on 127.0.0.1:{port} ({e}); start it with chrome-debug")
    finally:
        conn.close()


class CDP:
    """One websocket to the browser target: commands with ids, events to handlers.

    Trace hook: set `trace` to a callable(dir: 'send'|'resp'|'event', msg) and every
    CDP message in both directions is logged — the --trace proof that a replay run
    moves nothing but protocol traffic (zero model calls).
    """

    def __init__(self, port=DEFAULT_PORT, connect_timeout=10.0, call_timeout=30.0):
        url = browser_ws_url(port)
        host, _, rest = url.partition("://")
        if host != "ws":
            raise CDPError(f"unexpected browser endpoint {url!r}")
        hostport, path = rest.split("/", 1)
        h, _, p = hostport.partition(":")
        self.port = int(p or port)
        self.call_timeout = call_timeout
        self.trace = None
        self._id = 0
        self._id_lock = threading.Lock()
        self._pending = {}  # id -> [threading.Event, result, error]
        self._handlers = []
        self._ws = _Ws(h, self.port, "/" + path, timeout=connect_timeout)
        self._reader_error = None
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def on(self, callback):
        """Register an event callback fn(msg_dict); called on the reader thread."""
        self._handlers.append(callback)

    def _read_loop(self):
        try:
            while True:
                text = self._ws.recv_text()
                msg = json.loads(text)
                if "id" in msg and msg["id"] in self._pending:
                    slot = self._pending[msg["id"]]
                    if "error" in msg:
                        slot[2] = CDPError(f"{msg['error'].get('message', 'cdp error')}")
                    else:
                        slot[1] = msg.get("result", {})
                    if self.trace:
                        self.trace("resp", msg)
                    slot[0].set()
                elif "method" in msg:
                    if self.trace:
                        self.trace("event", msg)
                    for fn in self._handlers:
                        try:
                            fn(msg)
                        except Exception as e:  # a handler bug must not kill the reader
                            print(f"cdp: event handler error: {e}", flush=True)
                else:
                    if self.trace:
                        self.trace("other", msg)
        except Exception as e:
            self._reader_error = e
        finally:
            for slot in self._pending.values():
                slot[2] = CDPClosed(str(self._reader_error or "closed"))
                slot[0].set()
            self._pending.clear()

    def send(self, method, params=None, session_id=None, timeout=None):
        with self._id_lock:
            self._id += 1
            mid = self._id
        message = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        if self.trace:
            self.trace("send", message)
        slot = [threading.Event(), None, None]
        self._pending[mid] = slot
        try:
            self._ws.send_text(json.dumps(message))
        except Exception as e:
            del self._pending[mid]
            raise CDPClosed(f"send {method}: {e}")
        if not slot[0].wait(timeout or self.call_timeout):
            self._pending.pop(mid, None)
            raise CDPTimeout(f"timeout {method}")
        if slot[2] is not None:
            raise slot[2]
        return slot[1]

    def close(self):
        self._ws.close()
