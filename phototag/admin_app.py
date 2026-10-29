"""Local review admin (Flask). Static assets ship in-repo — no CDN."""
from __future__ import annotations

import secrets
from pathlib import Path

from phototag.config import (
    admin_bind,
    admin_password,
    admin_port,
    admin_session_secret,
    admin_token,
    admin_user,
    validate_admin_auth_config,
)
from phototag.review_queue import approve_image, list_review_rows, row_to_api_dict, skip_review

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
        expected_pw = admin_password()
        if (
            expected_user
            and expected_pw
            and secrets.compare_digest(user, expected_user)
            and secrets.compare_digest(pw, expected_pw)
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
        return render_template("index.html")

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


def run_server() -> None:
    app = create_app()
    host = admin_bind()
    port = admin_port()
    print(f"phototag admin: http://{host}:{port}/  (Login: PHOTOTAG_ADMIN_USER)", flush=True)
    app.run(host=host, port=port, debug=False)
