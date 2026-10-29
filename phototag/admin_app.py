"""Local review admin (Flask). Static assets ship in-repo — no CDN."""
from __future__ import annotations

from pathlib import Path

from phototag.config import admin_bind, admin_port, admin_token
from phototag.review_queue import approve_image, list_review_rows, row_to_api_dict, skip_review

_PKG = Path(__file__).resolve().parent
_STATIC = _PKG / "static" / "admin"
_TEMPLATES = _PKG / "templates" / "admin"


def create_app():
    try:
        from flask import Flask, abort, jsonify, render_template, request
    except ImportError as exc:
        raise RuntimeError(
            'Flask fehlt — installieren: pip install -e ".[admin]" '
            "(oder pip install flask)"
        ) from exc

    token = admin_token()
    if not token:
        raise RuntimeError(
            "PHOTOTAG_ADMIN_TOKEN fehlt — setze ein langes Geheimnis in phototag.env "
            "(Admin-UI startet nicht ohne Token)."
        )

    app = Flask(
        __name__,
        static_folder=str(_STATIC),
        static_url_path="/static",
        template_folder=str(_TEMPLATES),
    )

    def _auth() -> None:
        if request.headers.get("X-Phototag-Token") == token:
            return
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:] == token:
            return
        abort(401)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/tags")
    def api_tags():
        _auth()
        from phototag.cli import load_categories

        return jsonify({"tags": [t for t, _ in load_categories()]})

    @app.get("/api/review")
    def api_review_list():
        _auth()
        from phototag.cli import connect, load_categories

        con = connect()
        allowed = {t for t, _ in load_categories()}
        items = [row_to_api_dict(r, allowed) for r in list_review_rows(con)]
        return jsonify({"items": items, "count": len(items)})

    @app.get("/api/images/<int:image_id>/thumb")
    def api_thumb(image_id: int):
        _auth()
        from phototag.cli import connect

        con = connect()
        row = con.execute("SELECT thumb FROM images WHERE id=?", (image_id,)).fetchone()
        if not row or not row["thumb"]:
            abort(404)
        return app.response_class(row["thumb"], mimetype="image/jpeg")

    @app.post("/api/review/<int:image_id>/approve")
    def api_approve(image_id: int):
        _auth()
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
        _auth()
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
    app.run(host=admin_bind(), port=admin_port(), debug=False)
