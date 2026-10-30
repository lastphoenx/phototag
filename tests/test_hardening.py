"""Regressions: admin secrets, tag_one_row import, runaway model output."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from PIL import Image

from phototag import config
from phototag.cli import connect
from phototag.tagging import tag_one_row


def test_session_secret_has_no_hardcoded_default(monkeypatch):
    monkeypatch.delenv("PHOTOTAG_ADMIN_SESSION_SECRET", raising=False)
    monkeypatch.setenv("PHOTOTAG_ADMIN_TOKEN", "api-token")
    monkeypatch.setattr(config, "_ephemeral_session_secret", None)
    secret = config.admin_session_secret()
    assert secret not in ("phototag-admin-change-me", "api-token")
    assert len(secret) >= 32
    assert config.admin_session_secret() == secret  # stabil pro Prozess


def test_session_secret_explicit(monkeypatch):
    monkeypatch.setenv("PHOTOTAG_ADMIN_SESSION_SECRET", "abc")
    assert config.admin_session_secret() == "abc"


def _serve(reply: dict):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = json.dumps({"choices": [{"message": {"content": json.dumps(reply)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def env(tmp_path, monkeypatch):
    mount = tmp_path / "m"
    mount.mkdir()
    Image.new("RGB", (64, 64), "red").save(mount / "a.jpg")
    cat = tmp_path / "categories.txt"
    cat.write_text("\n".join(f"tag{i:02d}: x" for i in range(40)) + "\noutdoor: x\n", encoding="utf-8")
    monkeypatch.setenv("PHOTOTAG_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("PHOTOTAG_SCAN_ROOT", str(mount))
    monkeypatch.setenv("PHOTOTAG_CATEGORIES", str(cat))
    return mount


def _row(con, mount: Path):
    con.execute(
        "INSERT INTO images(root,path,folder,kind,status) VALUES(?,?,?,?,?)",
        (str(mount), "a.jpg", "", "image", "new"),
    )
    con.commit()
    return con.execute("SELECT * FROM images").fetchone()


def test_tag_one_row_runs_and_tags(env, monkeypatch):
    srv = _serve({"age_status": "yes", "tags": ["outdoor"], "caption": "x"})
    monkeypatch.setenv("PHOTOTAG_API", f"http://127.0.0.1:{srv.server_port}/v1")
    con = connect()
    res = tag_one_row(con, _row(con, env))
    assert res["ok"] and res["status"] == "tagged"


def test_runaway_tag_list_goes_to_review(env, monkeypatch):
    every = [f"tag{i:02d}" for i in range(40)]
    srv = _serve({"age_status": "yes", "tags": every, "caption": "x"})
    monkeypatch.setenv("PHOTOTAG_API", f"http://127.0.0.1:{srv.server_port}/v1")
    con = connect()
    res = tag_one_row(con, _row(con, env))
    assert res["status"] == "review"
    err = con.execute("SELECT error FROM images").fetchone()[0]
    assert "too_many_tags" in err
