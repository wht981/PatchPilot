"""Scripted model adapters: repeatable agent tests without credentials."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from patchpilot.llm import ModelReply


def read_action(path):
    return {"action": "read_file", "path": path}


def plan_action(paths):
    return {
        "action": "plan", "issue_summary": "Repair the reported behavior",
        "suspected_root_cause": "The implementation disagrees with the tests",
        "candidate_files": paths, "proposed_changes": "Correct the source logic",
        "test_strategy": "Run the unchanged detected test suite",
        "risk_notes": "Keep unrelated behavior and test definitions intact",
    }


def patch_action(edits):
    return {"action": "patch", "analysis": "Correct the implementation using repository evidence", "edits": edits}


class ScriptedClient:
    model = "scripted-model"

    def __init__(self, actions):
        self.actions = list(actions)
        self.requests = []

    def complete(self, messages):
        self.requests.append(json.loads(json.dumps(messages)))
        if not self.actions:
            raise AssertionError("Unexpected model request")
        action = self.actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        return ModelReply(action if isinstance(action, str) else json.dumps(action), 10, 5)


@contextmanager
def model_server(actions):
    state = {"actions": list(actions), "requests": [], "headers": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append(body)
            state["headers"].append(dict(self.headers))
            action = state["actions"].pop(0)
            status = action if isinstance(action, int) else 200
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if isinstance(action, int):
                self.wfile.write(b'{"error":"provider failed"}')
            else:
                reply = {"choices": [{"finish_reason": "stop", "message": {
                    "role": "assistant", "content": json.dumps(action)}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
                self.wfile.write(json.dumps(reply).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
