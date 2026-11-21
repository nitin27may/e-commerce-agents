"""scripts/azure/smoke.sh against a stub frontend: no Azure, no compose, no model.

The stub speaks just enough of the API (login, products, orders, chat stream, approvals, timeline) to drive
every step of the script, so a change to the script's parsing or its pass/fail rules is caught offline.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "azure" / "smoke.sh"
pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("curl") is None, reason="needs bash and curl"
)

CARD = '```product\n{"id": "0fd372fa-ecb2-4db0-bb71-8628a784ced9", "name": "Laptop", "price": 999}\n```'


def _make_handler(state: dict[str, Any]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def _json(self, payload: object, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")
            elif self.path.startswith("/api/products"):
                self._json({"products": [{"id": "p1"}], "total": 1})
            elif self.path.startswith("/api/orders"):
                self._json({"orders": [{"status": "delivered", "total": "750.00"}]})
            elif self.path.startswith("/api/approvals"):
                self._json(
                    {
                        "approvals": [
                            {
                                "id": "a1",
                                "requester_email": "alice.johnson@gmail.com",
                                "status": "pending_review",
                                "assigned_role": state["route"],
                                "route": state["route"],
                            }
                        ]
                    }
                )
            elif "/timeline" in self.path:
                self._json({"entries": [{"title": "Approved"}, {"title": "State saved"}]})
            else:
                self._json({}, 404)

        def do_POST(self) -> None:  # noqa: N802
            body = self._body()
            if self.path == "/api/auth/login":
                if body.get("password") == "wrong":
                    self._json({}, 401)
                else:
                    state["logins"].append(body["email"])
                    self._json({"access_token": "t-" + body["email"]})
            elif self.path == "/api/chat/stream":
                frames = [f"data: {line}\n\n" for line in (["Here"] + [f"w{i}" for i in range(12)])]
                if body.get("mode") == "workflow:return-replace":
                    frames = ['event: run\ndata: {"run_id": "r1", "pending_approval": true}\n\n']
                else:
                    frames = [*frames[:3], *[f"data: {line}\n\n" for line in CARD.split("\n")][:0], *frames[3:]]
                    card = "".join(f"data: {line}\n" for line in CARD.split("\n")) + "\n"
                    frames.append(card)
                payload = "".join(frames + ["data: [DONE]\n\n"]).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif self.path == "/api/approvals/a1/decide":
                state["decided_by"] = self.headers.get("Authorization")
                self._json({"status": "approved"})
            else:
                self._json({}, 404)

    return Handler


@pytest.fixture()
def stub() -> Any:
    state: dict[str, Any] = {"route": "manager", "logins": [], "decided_by": None}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _run(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AZURE_")}
    env.update(FRONTEND_URL=url, E2E_STAFF_PASSWORD="Staff-pw-1")
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60, check=False
    )


def test_quick_passes_and_skips_azure_checks(stub: Any) -> None:
    state, url = stub
    result = _run(url, "--quick")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: quick smoke" in result.stdout
    assert "skip  revision and job checks" in result.stdout
    assert {"admin.demo@gmail.com", "alice.johnson@gmail.com"} <= set(state["logins"])


def test_full_run_decides_a_manager_route_as_admin(stub: Any) -> None:
    state, url = stub
    result = _run(url)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: full smoke" in result.stdout
    assert state["decided_by"] == "Bearer t-admin.demo@gmail.com"
    assert "support.demo@gmail.com" not in state["logins"]


def test_full_run_uses_support_for_a_support_route(stub: Any) -> None:
    state, url = stub
    state["route"] = "support"
    result = _run(url)
    assert result.returncode == 0, result.stdout + result.stderr
    assert state["decided_by"] == "Bearer t-support.demo@gmail.com"


def test_missing_frontend_url_fails() -> None:
    env = {k: v for k, v in os.environ.items() if k != "FRONTEND_URL"}
    result = subprocess.run(["bash", str(SCRIPT), "--quick"], capture_output=True, text=True, env=env, check=False)
    assert result.returncode == 1
    assert "FRONTEND_URL is not set" in result.stderr
