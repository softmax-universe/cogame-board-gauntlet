#!/usr/bin/env python3
"""Deterministic local Anthropic Messages HTTP fixture for Board Gauntlet smoke/certification.

Serves POST /v1/messages on 0.0.0.0:<port> and responds with the first canonical
legal move listed in the Board Gauntlet user prompt. Never calls any external LLM.
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LEGAL_MOVES_RE = re.compile(r"YOUR LEGAL MOVES[^\n]*exactly:\s*([^\n]+)")


def pick_legal_move(user_text: str) -> str:
    match = LEGAL_MOVES_RE.search(user_text)
    if not match:
        raise ValueError("no legal-move list found in user prompt")
    moves = match.group(1).strip().split()
    if not moves:
        raise ValueError("empty legal-move list in user prompt")
    return moves[0]


def make_handler(count_file: Path | None, default_model: str):
    state = {"count": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.startswith("/healthz"):
                body = b"ok\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:
            if self.path != "/v1/messages":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length)
            payload = json.loads(raw_body.decode("utf-8"))
            messages = payload.get("messages") or []
            user_text = ""
            if messages and isinstance(messages[-1], dict):
                content = messages[-1].get("content", "")
                if isinstance(content, str):
                    user_text = content
            move = pick_legal_move(user_text)
            reply = json.dumps({"move": move, "say": "fixture move", "notes": "fixture"})
            call_id = str(uuid.uuid4())
            state["count"] += 1
            if count_file is not None:
                count_file.write_text(str(state["count"]) + "\n", encoding="utf-8")
            response = json.dumps(
                {
                    "id": call_id,
                    "type": "message",
                    "role": "assistant",
                    "model": payload.get("model") or default_model,
                    "content": [{"type": "text", "text": reply}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 16, "output_tokens": 12},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.send_header("X-Softmax-Llm-Call-Id", call_id)
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: object) -> None:
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--port-file", type=Path, default=None)
    parser.add_argument("--count-file", type=Path, default=None)
    parser.add_argument("--model", default="fixture/board-gauntlet")
    args = parser.parse_args()

    if args.count_file is not None:
        args.count_file.write_text("0\n", encoding="utf-8")

    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.count_file, args.model))
    actual_port = server.server_address[1]
    if args.port_file is not None:
        args.port_file.write_text(f"{actual_port}\n", encoding="utf-8")
    print(f"fixture listening on {args.host}:{actual_port}", flush=True)

    def handle_stop(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, handle_stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
