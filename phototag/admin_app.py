"""Local review admin (Flask). Static assets ship in-repo — no CDN."""
from __future__ import annotations

import secrets
from pathlib import Path

from phototag.config import (
    admin_bind,
    admin_port,
    admin_session_secret,
    admin_token,
    admin_user,
    scan_root,
    validate_admin_auth_config,
    verify_admin_password,
)
from phototag.review_queue import approve_image, list_review_rows, row_to_api_dict, skip_review
from phototag.admin_browse import (
    mount_accessible,
    normalize_folder,
    resolve_images_root,
)
from phototag.admin_mount import (
    list_child_folders_mount_only,
    list_mount_browse_items,
    save_mount_tags,
    thumb_bytes_for_nc,
)

_PKG = Path(__file__).resolve().parent
_STATIC = _PKG / "static" / "admin"
_TEMPLATES = _PKG / "templates" / "admin"


def create_app():
    try:
        from flask import (
            Flask,
            abort,
            jsonify,
            redirect,
            render_template,
            request,
            session,
            url_for,
        )
    except ImportError as exc:
        raise RuntimeError(
            'Flask fehlt — installieren: pip install -e ".[admin]" '
            "(oder pip install flask)"
        ) from exc

    validate_admin_auth_config()

    app = Flask(
        __name__,
        static_folder=str(_STATIC),
        static_url_path="/static",
        template_folder=str(_TEMPLATES),
    )
    app.secret_key = admin_session_secret()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

    def _logged_in() -> bool:
        return bool(session.get("admin"))

    def _check_api_auth() -> None:
        if _logged_in():
            return
        tok = admin_token()
        if tok and request.headers.get("X-Phototag-Token") == tok:
            return
        auth = request.headers.get("Authorization", "")
        if tok and auth.startswith("Bearer ") and auth[7:] == tok:
            return
        abort(401)

    @app.before_request
    def _guard():
        if request.endpoint in (None, "static", "login", "login_post"):
            return
        if request.path.startswith("/api/"):
            if not _logged_in():
                _check_api_auth()
            return
        if not _logged_in():
            return redirect(url_for("login"))

    @app.get("/login")
    def login():
        if _logged_in():
            return redirect(url_for("index"))
        err = request.args.get("err", "")
        return render_template("login.html", error=err)

    @app.post("/login")
    def login_post():
        user = (request.form.get("username") or "").strip()
        pw = request.form.get("password") or ""
        expected_user = admin_user()
        if (
            expected_user
            and secrets.compare_digest(user, expected_user)
            and verify_admin_password(pw)
        ):
            session.clear()
            session["admin"] = True
            session.permanent = False
            return redirect(url_for("index"))
        return redirect(url_for("login", err="1"))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    def index():
        from phototag import __version__

        return render_template("index.html", app_version=__version__)

    @app.get("/api/tags")
    def api_tags():
        _check_api_auth()
        from phototag.cli import load_categories

        return jsonify({"tags": [t for t, _ in load_categories()]})

    @app.get("/api/review")
    def api_review_list():
        _check_api_auth()
        from phototag.cli import connect, load_categories

        con = connect()
        allowed = {t for t, _ in load_categories()}
        items = [row_to_api_dict(r, allowed) for r in list_review_rows(con)]
        return jsonify({"items": items, "count": len(items)})

    @app.get("/api/browse/folders")
    def api_browse_folders():
        _check_api_auth()
        from phototag.cli import connect

        con = connect()
        root = resolve_images_root(con)
        parent = normalize_folder(request.args.get("folder", "") or "")
        folders = list_child_folders_mount_only(con, root, parent)
        return jsonify(
            {
                "folder": parent,
                "root": root,
                "folders": folders,
                "mount_ok": mount_accessible(),
                "scan_root": str(Path(scan_root()).resolve()) if mount_accessible() else "",
            }
        )

    @app.get("/api/browse")
    def api_browse_list():
        _check_api_auth()
        from phototag.cli import connect, load_categories

        con = connect()
        folder = normalize_folder(request.args.get("folder", "") or "")
        filter_key = (request.args.get("filter") or "all").strip().lower()
        limit = request.args.get("limit", 80, type=int)
        offset = request.args.get("offset", 0, type=int)
        allowed = {t for t, _ in load_categories()}
        if not mount_accessible():
            return jsonify({"ok": False, "error": "PHOTOTAG_SCAN_ROOT nicht lesbar (Mount?)"}), 503
        items, total = list_mount_browse_items(
            con, folder, filter_key, allowed, limit=limit, offset=offset
        )
        return jsonify(
            {
                "items": items,
                "count": len(items),
                "total": total,
                "folder": folder,
                "filter": filter_key,
                "source": "mount",
            }
        )

    @app.get("/api/browse/thumb")
    def api_browse_thumb():
        _check_api_auth()
        from phototag.cli import connect

        nc_path = (request.args.get("nc_path") or "").strip().lstrip("/")
        if not nc_path:
            abort(400)
        con = connect()
        blob = thumb_bytes_for_nc(con, nc_path)
        if not blob:
            abort(404)
        return app.response_class(blob, mimetype="image/jpeg")

    @app.post("/api/browse/save")
    def api_browse_save():
        _check_api_auth()
        from phototag.cli import connect, load_categories

        push = request.args.get("push", "0") in ("1", "true", "yes")
        body = request.get_json(silent=True) or {}
        nc_path = (body.get("nc_path") or "").strip().lstrip("/")
        tags = body.get("tags")
        caption = body.get("caption", "")
        if not nc_path:
            return jsonify({"ok": False, "error": "nc_path fehlt"}), 400
        if not isinstance(tags, list):
            return jsonify({"ok": False, "error": "tags muss Array sein"}), 400
        con = connect()
        allowed = {t for t, _ in load_categories()}
        try:
            result = save_mount_tags(
                con,
                nc_path,
                tags=tags,
                caption=str(caption),
                push_nc=push,
                allowed=allowed,
            )
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify(result)

    @app.post("/api/tag/one")
    def api_tag_one():
        _check_api_auth()
        from phototag.cli import connect
        from phototag.tag_jobs import tag_nc_path_sync

        body = request.get_json(silent=True) or {}
        nc_path = (body.get("nc_path") or "").strip().lstrip("/")
        retag = body.get("retag") in (True, 1, "1", "true")
        if not nc_path:
            return jsonify({"ok": False, "error": "nc_path fehlt"}), 400
        if not mount_accessible():
            return jsonify(
                {"ok": False, "error": "PHOTOTAG_SCAN_ROOT nicht lesbar — phototag-rclone-mount.service starten"},
            ), 503
        con = connect()
        try:
            result = tag_nc_path_sync(con, nc_path, retag=retag)
        except (FileNotFoundError, RuntimeError, OSError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify(result)

    @app.post("/api/tag/jobs")
    def api_tag_jobs_create():
        _check_api_auth()
        from phototag.cli import connect
        from phototag.tag_jobs import create_tag_job, get_tag_job, spawn_tag_job_subprocess

        body = request.get_json(silent=True) or {}
        folders = body.get("folders") or []
        nc_paths = body.get("nc_paths") or []
        if isinstance(folders, str):
            folders = [folders]
        if isinstance(nc_paths, str):
            nc_paths = [nc_paths]
        retag = body.get("retag") in (True, 1, "1", "true")
        recursive = body.get("recursive", True) not in (False, 0, "0", "false")
        kind = body.get("kind")
        limit = int(body.get("limit") or 0)
        if not folders and not nc_paths:
            return jsonify({"ok": False, "error": "folders oder nc_paths nötig"}), 400
        if not mount_accessible():
            return jsonify(
                {"ok": False, "error": "PHOTOTAG_SCAN_ROOT nicht lesbar — phototag-rclone-mount.service starten"},
            ), 503
        con = connect()
        job_id = create_tag_job(
            con,
            folders=[str(f) for f in folders],
            nc_paths=[str(p) for p in nc_paths],
            retag=retag,
            recursive=recursive,
            kind=kind,
            limit=limit,
        )
        job = get_tag_job(con, job_id)
        if job and job["total"] == 0:
            return jsonify({"ok": False, "error": "Keine Dateien für Job", "job": job}), 400
        spawn_tag_job_subprocess(job_id)
        return jsonify({"ok": True, "job": job})

    @app.get("/api/tag/jobs/<job_id>")
    def api_tag_job_status(job_id: str):
        _check_api_auth()
        from phototag.cli import connect
        from phototag.tag_jobs import get_tag_job

        con = connect()
        job = get_tag_job(con, job_id)
        if not job:
            return jsonify({"ok": False, "error": "Job nicht gefunden"}), 404
        return jsonify({"ok": True, "job": job})

    @app.get("/api/images/<int:image_id>/thumb")
    def api_thumb(image_id: int):
        _check_api_auth()
        from phototag.cli import connect

        con = connect()
        row = con.execute("SELECT thumb FROM images WHERE id=?", (image_id,)).fetchone()
        if not row or not row["thumb"]:
            abort(404)
        return app.response_class(row["thumb"], mimetype="image/jpeg")

    @app.post("/api/review/<int:image_id>/approve")
    def api_approve(image_id: int):
        _check_api_auth()
        from phototag.cli import connect

        push = request.args.get("push", "0") in ("1", "true", "yes")
        body = request.get_json(silent=True) or {}
        tags = body.get("tags")
        caption = body.get("caption")
        if tags is not None and not isinstance(tags, list):
            return jsonify({"ok": False, "error": "tags muss Array sein"}), 400
        con = connect()
        try:
            result = approve_image(
                con,
                image_id,
                push_nc=push,
                tags=tags,
                caption=str(caption) if caption is not None else None,
            )
        except (LookupError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, **result})

    @app.post("/api/review/<int:image_id>/skip")
    def api_skip(image_id: int):
        _check_api_auth()
        from phototag.cli import connect

        con = connect()
        try:
            skip_review(con, image_id)
        except (LookupError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "id": image_id})

    return app


# gunicorn entry (scripts/run-admin-wsgi.sh): phototag.admin_app:application
application = create_app()


def run_server() -> None:
    app = application
    host = admin_bind()
    port = admin_port()
    print("=== phototag Review-Admin (Web-UI) ===", flush=True)
    print(
        "Login im Browser: Benutzername + Passwort aus phototag.env "
        "(PHOTOTAG_ADMIN_USER / PHOTOTAG_ADMIN_PASSWORD oder PASSWORD_HASH).",
        flush=True,
    )
    print(f"Lauscht auf http://{host}:{port}/", flush=True)
    if host in ("127.0.0.1", "localhost", "::1"):
        print(
            "HINWEIS: PHOTOTAG_ADMIN_BIND=127.0.0.1 — nur auf diesem CT erreichbar. "
            "Fuer PC im LAN: BIND=0.0.0.0 setzen, dann http://<CT-IP>:8765/",
            flush=True,
        )
    else:
        print(
            f"Vom PC: http://<IP-dieses-CT>:{port}/  (Port {port} = PHOTOTAG_ADMIN_PORT)",
            flush=True,
        )
    print(
        "Dauerbetrieb: ./scripts/install-admin-service.sh (gunicorn + systemd).",
        flush=True,
    )
    tok = admin_token()
    if tok:
        print(
            "PHOTOTAG_ADMIN_TOKEN: nur fuer API (Header X-Phototag-Token), nicht fuer Browser-Login.",
            flush=True,
        )
    app.run(host=host, port=port, debug=False)
