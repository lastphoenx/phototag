"""Blur UI options and API defaults."""
from __future__ import annotations

import json

import pytest

from phototag.blur_options import (
    apply_blur_options_overlay,
    blur_defaults_payload,
    normalize_blur_options,
    options_for_storage,
    validate_blur_options_body,
)


def test_defaults_lists_presets_and_parts():
    payload = blur_defaults_payload()
    ids = [p["id"] for p in payload["presets"]]
    assert ids[0] == "env"
    assert "eyebar" in ids and "custom" in ids
    assert "eyebar" in payload["parts"]
    assert "score" not in str(payload)


def test_env_preset_stores_nothing():
    assert options_for_storage({"preset": "env"}) is None
    opts, src = normalize_blur_options({"preset": "env"})
    assert opts is None and src == "env"


def test_eyebar_preset_resolves():
    opts, src = normalize_blur_options({"preset": "eyebar"})
    assert src == "ui" and opts["region"] == "eyebar" and opts["fill"] == "none"


def test_custom_requires_parts(monkeypatch):
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_REGION", "face")
    opts, errs = validate_blur_options_body(
        {"preset": "custom", "region": "bogus", "fill": "none", "margin": 0.1, "strength": 1}
    )
    assert opts is None and errs


def test_overlay_sets_env(monkeypatch):
    monkeypatch.delenv("PHOTOTAG_FACEBLUR_REGION", raising=False)
    src = apply_blur_options_overlay({"preset": "eyes"})
    assert src == "ui"
    assert "eyes" in __import__("os").environ["PHOTOTAG_FACEBLUR_REGION"]


def test_blur_defaults_api(client, monkeypatch):
    monkeypatch.setenv("PHOTOTAG_FACEBLUR_MODEL", "/nonexistent/model.onnx")
    from phototag.config import faceblur_enabled

    if not faceblur_enabled():
        pytest.skip("model path")
    r = client.get("/api/blur/defaults")
    assert r.status_code == 200 and r.json["ok"] and r.json["presets"]


def test_blur_job_accepts_options(client, mount_env, monkeypatch):
    from phototag import blur_jobs
    from phototag.cli import connect

    monkeypatch.setenv("PHOTOTAG_FACEBLUR_MODEL", "")
    r = client.post(
        "/api/blur/jobs",
        json={"nc_path": "Album/a.png", "options": {"preset": "eyebar"}},
    )
    if r.status_code == 503:
        pytest.skip("faceblur off")
    assert r.status_code in (200, 409)
    if r.status_code == 200:
        row = connect().execute("SELECT options FROM blur_jobs WHERE id=?", (r.json["job"]["id"],)).fetchone()
        assert row["options"] and "eyebar" in row["options"]
