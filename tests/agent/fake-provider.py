#!/usr/bin/env python3
"""TEST ONLY: a local OpenAI-compatible chat endpoint, so a real agent (opencode, Codex) can run
headless with no account and no model. Logs every request body to LOG (one JSON per line).

The reply depends on the last user message:
  contains SLOW    stream "ok" slowly over SLOW_S seconds (default 8): the agent is working
  contains TOOL    call the shell tool with `rm -rf build` (the agent asks for permission),
                   then answer "done" once the tool result comes back
  otherwise        answer "PONG"

  fake-provider.py PORT LOG
Serves POST /v1/chat/completions (stream or not) and POST /v1/responses (Codex's API, streamed).
"""
import json, sys, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, LOG = int(sys.argv[1]), sys.argv[2]
SLOW_S = float(__import__("os").environ.get("SLOW_S", "8"))


def last_user(msgs):
    for m in reversed(msgs):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
            return c or ""
    return ""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self.send_json({"object": "list", "data": [{"id": "m1", "object": "model"}]})
        self.send_json({}, 404)

    def send_json(self, body, code=200):
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def sse(self, events):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for delay, ev in events:
            time.sleep(delay)
            self.wfile.write(ev.encode())
            self.wfile.flush()
        self.close_connection = True

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        with open(LOG, "a") as f:
            f.write(json.dumps({"path": self.path, "body": body}) + "\n")
        if self.path.endswith("/chat/completions"):
            return self.chat(body)
        if self.path.endswith("/responses"):
            return self.responses_api(body)
        self.send_json({"error": "not found"}, 404)

    # ---- OpenAI chat completions (opencode via @ai-sdk/openai-compatible)
    def chat(self, body):
        msgs = body.get("messages", [])
        text = last_user(msgs)
        tool_done = msgs and msgs[-1].get("role") == "tool"
        cid, model = "chatcmpl-" + uuid.uuid4().hex[:8], body.get("model", "m1")

        def chunk(delta, finish=None):
            return "data: " + json.dumps({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                                          "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"
        tools = [t["function"]["name"] for t in body.get("tools", []) if t.get("type") == "function"]
        shell = next((n for n in ("bash", "shell") if n in tools), None)
        if "TOOL" in text and not tool_done and shell:
            call = {"index": 0, "id": "call_" + uuid.uuid4().hex[:8], "type": "function",
                    "function": {"name": shell, "arguments": json.dumps({"command": "rm -rf build", "description": "Remove the build folder"})}}
            events = [(0, chunk({"role": "assistant", "tool_calls": [call]})), (0, chunk({}, "tool_calls"))]
        elif "SLOW" in text and not tool_done:
            n = 8
            events = [(0, chunk({"role": "assistant", "content": ""}))] + \
                     [(SLOW_S / n, chunk({"content": "ok "})) for _ in range(n)] + [(0, chunk({}, "stop"))]
        else:
            events = [(0, chunk({"role": "assistant", "content": "done" if tool_done else "PONG"})), (0, chunk({}, "stop"))]
        if not body.get("stream"):
            return self.send_json({"id": cid, "object": "chat.completion", "model": model,
                              "choices": [{"index": 0, "message": {"role": "assistant", "content": "PONG"}, "finish_reason": "stop"}]})
        self.sse(events + [(0, "data: [DONE]\n\n")])

    # ---- OpenAI Responses API (Codex)
    def responses_api(self, body):
        inp = body.get("input", [])
        msgs = [{"role": i.get("role"), "content": i.get("content")} for i in inp if isinstance(i, dict) and i.get("type", "message") == "message"]
        text = last_user(msgs)
        tool_done = any(isinstance(i, dict) and i.get("type") == "function_call_output" for i in inp[-2:])
        rid = "resp_" + uuid.uuid4().hex[:8]

        def ev(kind, **kw):
            return f"event: {kind}\ndata: " + json.dumps({"type": kind, **kw}) + "\n\n"
        resp = {"id": rid, "object": "response", "status": "in_progress", "output": []}
        events = [(0, ev("response.created", response=resp))]
        tools = [t.get("name") for t in body.get("tools", [])]
        if "TOOL" in text and not tool_done and ("shell" in tools or "exec_command" in tools):
            name = "shell" if "shell" in tools else "exec_command"
            # ask to run outside the sandbox, so the agent shows its approval prompt
            args = ({"command": ["bash", "-lc", "rm -rf build"], "with_escalated_permissions": True, "justification": "Remove the build folder?"}
                    if name == "shell" else
                    {"cmd": "rm -rf build", "sandbox_permissions": "require_escalated", "justification": "Remove the build folder?"})
            item = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": name, "arguments": json.dumps(args)}
            events += [(0, ev("response.output_item.done", output_index=0, item=item))]
        else:
            words = ["ok "] * 8 if "SLOW" in text and not tool_done else ["done" if tool_done else "PONG"]
            gap = SLOW_S / len(words) if len(words) > 1 else 0
            item = {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": "".join(words), "annotations": []}]}
            events += [(0, ev("response.output_item.added", output_index=0, item=dict(item, content=[], status="in_progress")))]
            events += [(gap, ev("response.output_text.delta", output_index=0, content_index=0, item_id="msg_1", delta=w)) for w in words]
            events += [(0, ev("response.output_item.done", output_index=0, item=item))]
        done = dict(resp, status="completed", output=[item],
                    usage={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
                           "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}})
        events += [(0, ev("response.completed", response=done))]
        self.sse(events)


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
