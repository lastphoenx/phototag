"""Local review admin (Flask). Static assets ship in-repo — no CDN."""
from __future__ import annotations

import re
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
    forward_mount_media_to_telegram,
    list_child_folders_mount_only,
    list_mount_browse_items,
    save_mount_tags,
    thumb_bytes_for_nc,
)
from phototag.config import faceblur_enabled, faceblur_mode, faceblur_region, faceblur_strength
from phototag.telegram_forward import telegram_configured

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
        if tok and secrets.compare_digest(request.headers.get("X-Phototag-Token", ""), tok):
            return
        auth = request.headers.get("Authorization", "")
        if tok and auth.startswith("Bearer ") and secrets.compare_digest(auth[7:], tok):
            return
        abort(401)

    def _thumb_response(blob: bytes):
        """JPEG with ETag + no-cache: re-created files with the same name get a fresh preview."""
        import hashlib

        resp = app.response_class(blob, mimetype="image/jpeg")
        resp.set_etag(hashlib.md5(blob).hexdigest())
        resp.headers["Cache-Control"] = "private, no-cache"
        return resp.make_conditional(request)

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

        return render_template(
            "index.html",
            app_version=__version__,
            telegram_enabled=telegram_configured(),
            faceblur_enabled=faceblur_enabled(),
            faceblur_region=faceblur_region(),
            faceblur_strength=faceblur_strength(),
            faceblur_mode=faceblur_mode(),
        )

    @app.get("/api/tags")
    def api_tags():
        _check_api_auth()
        from phototag.cli import load_categories

        return jsonify({"tags": [t for t, _ in load_categories()]})

    @app.get("/api/search")
    def api_search():
        _check_api_auth()
        from phototag.cli import connect, load_categories
        from phototag.admin_mount import _IMAGES_BROWSE_COLS, _mount_entry_from_row
        from phototag.admin_search import search_image_rows
        from phototag.blur_jobs import blur_copies_by_source
        from phototag.nextcloud_sync import nc_dav_relative_path

        con = connect()
        allowed = {t for t, _ in load_categories()}
        q = (request.args.get("q") or request.args.get("query") or "").strip()
        raw_tags = request.args.getlist("tag")
        if not raw_tags:
            one = (request.args.get("tags") or "").strip()
            if one:
                raw_tags = [p.strip() for p in re.split(r"[,;+]+", one) if p.strip()]
        tags = [t.lower().replace(" ", "_") for t in raw_tags if t.strip()]
        any_term = request.args.get("any", "0") in ("1", "true", "yes")
        fuzzy = request.args.get("fuzzy", "0") in ("1", "true", "yes")
        folder = (request.args.get("folder") or "").strip()
        kind = (request.args.get("kind") or "").strip() or None
        limit = request.args.get("limit", 80, type=int)
        offset = request.args.get("offset", 0, type=int)
        if not q and not tags and not folder and not kind:
            return jsonify({"ok": False, "error": "q, tag, folder oder kind angeben"}), 400
        sel = ", ".join(f"i.{c.strip()}" for c in _IMAGES_BROWSE_COLS.split(","))
        rows, total = search_image_rows(
            con,
            query=q,
            tags=tags or None,
            any_term=any_term,
            fuzzy=fuzzy,
            folder=folder,
            kind=kind,
            limit=limit,
            offset=offset,
            columns=sel,
        )
        blur_map = blur_copies_by_source(con)
        items = []
        for row in rows:
            nc_rel = nc_dav_relative_path(row["root"], row["path"])
            items.append(_mount_entry_from_row(con, nc_rel, row, allowed, blur_map))
        return jsonify(
            {
                "ok": True,
                "items": items,
                "count": len(items),
                "total": total,
                "query": q,
                "tags": tags,
                "any": any_term,
                "fuzzy": fuzzy,
                "folder": folder,
                "kind": kind,
                "offset": max(0, int(offset)),
                "limit": max(1, min(200, int(limit))),
                "source": "db",
            }
        )

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
        recursive = request.args.get("recursive", "0") in ("1", "true", "yes")
        if filter_key == "blurred":
            recursive = True
        allowed = {t for t, _ in load_categories()}
        if not mount_accessible():
            return jsonify({"ok": False, "error": "PHOTOTAG_SCAN_ROOT nicht lesbar (Mount?)"}), 503
        items, total = list_mount_browse_items(
            con,
            folder,
            filter_key,
            allowed,
            limit=limit,
            offset=offset,
            recursive=recursive,
        )
        return jsonify(
            {
                "items": items,
                "count": len(items),
                "total": total,
                "folder": folder,
                "filter": filter_key,
                "recursive": recursive,
                "offset": max(0, int(offset)),
                "limit": max(1, min(200, int(limit))),
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
        return _thumb_response(blob)

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

    @app.post("/api/browse/telegram")
    def api_browse_telegram():
        _check_api_auth()
        if not telegram_configured():
            return jsonify(
                {
                    "ok": False,
                    "error": "Telegram nicht konfiguriert (PHOTOTAG_TELEGRAM_BOT_TOKEN + CHAT_ID)",
                },
            ), 503
        body = request.get_json(silent=True) or {}
        nc_path = (body.get("nc_path") or "").strip().lstrip("/")
        caption = body.get("caption", "")
        if not nc_path:
            return jsonify({"ok": False, "error": "nc_path fehlt"}), 400
        if not mount_accessible():
            return jsonify(
                {"ok": False, "error": "PHOTOTAG_SCAN_ROOT nicht lesbar"},
            ), 503
        try:
            result = forward_mount_media_to_telegram(nc_path, caption=str(caption))
        except FileNotFoundError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        except RuntimeError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 502
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

    @app.get("/api/tag/jobs/preview")
    def api_tag_jobs_preview():
        _check_api_auth()
        from phototag.cli import connect
        from phototag.tag_jobs import preview_tag_job

        folder = normalize_folder(request.args.get("folder", "") or "")
        folders = [folder]
        retag = request.args.get("retag", "0") in ("1", "true", "yes")
        recursive = request.args.get("recursive", "1") not in ("0", "false", "no")
        if not mount_accessible():
            return jsonify(
                {"ok": False, "error": "PHOTOTAG_SCAN_ROOT nicht lesbar — phototag-rclone-mount.service starten"},
            ), 503
        con = connect()
        stats = preview_tag_job(
            con,
            folders=folders,
            retag=retag,
            recursive=recursive,
        )
        return jsonify({"ok": True, **stats})

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
        try:
            limit = max(0, int(body.get("limit") or 0))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "limit muss Zahl sein"}), 400
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

    @app.post("/api/blur/jobs")
    def api_blur_job_create():
        _check_api_auth()
        from phototag.blur_jobs import (
            BlurBusy,
            create_blur_job,
            get_blur_job,
            spawn_blur_job_subprocess,
        )
        from phototag.cli import connect
        from phototag.faceblur import FaceBlurError

        if not faceblur_enabled():
            return jsonify({"ok": False, "error": "Face-Blur nicht aktiv (PHOTOTAG_FACEBLUR_MODEL)"}), 503
        body = request.get_json(silent=True) or {}
        nc_path = (body.get("nc_path") or "").strip().lstrip("/")
        if not nc_path:
            return jsonify({"ok": False, "error": "nc_path fehlt"}), 400
        if not mount_accessible():
            return jsonify({"ok": False, "error": "PHOTOTAG_SCAN_ROOT nicht lesbar"}), 503
        from phototag.blur_options import validate_blur_options_body

        opts_raw = body.get("options")
        if opts_raw is not None and not isinstance(opts_raw, dict):
            return jsonify({"ok": False, "error": "options muss Objekt sein"}), 400
        options, opt_errors = validate_blur_options_body(opts_raw if isinstance(opts_raw, dict) else None)
        if opt_errors and options is None:
            return jsonify({"ok": False, "error": "; ".join(opt_errors)}), 400
        con = connect()
        try:
            job_id = create_blur_job(
                con, nc_path, options=opts_raw if isinstance(opts_raw, dict) else None
            )
        except BlurBusy as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        except FileNotFoundError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        except (FaceBlurError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        spawn_blur_job_subprocess(job_id)
        job = get_blur_job(con, job_id)
        if opt_errors:
            job = {**job, "option_warnings": opt_errors}
        return jsonify({"ok": True, "job": job})

    @app.get("/api/blur/defaults")
    def api_blur_defaults():
        _check_api_auth()
        if not faceblur_enabled():
            return jsonify({"ok": False, "error": "Face-Blur nicht aktiv"}), 503
        from phototag.blur_options import blur_defaults_payload

        return jsonify({"ok": True, **blur_defaults_payload()})

    @app.post("/api/blur/preview")
    def api_blur_preview():
        _check_api_auth()
        from phototag.blur_jobs import blur_preview_for_nc, validate_source
        from phototag.blur_options import validate_blur_options_body
        from phototag.faceblur import FaceBlurError, NoFacesFound

        if not faceblur_enabled():
            return jsonify({"ok": False, "error": "Face-Blur nicht aktiv"}), 503
        body = request.get_json(silent=True) or {}
        nc_path = (body.get("nc_path") or "").strip().lstrip("/")
        if not nc_path:
            return jsonify({"ok": False, "error": "nc_path fehlt"}), 400
        opts_raw = body.get("options")
        if opts_raw is not None and not isinstance(opts_raw, dict):
            return jsonify({"ok": False, "error": "options muss Objekt sein"}), 400
        options, opt_errors = validate_blur_options_body(opts_raw if isinstance(opts_raw, dict) else None)
        if opt_errors and options is None:
            return jsonify({"ok": False, "error": "; ".join(opt_errors)}), 400
        if not mount_accessible():
            return jsonify({"ok": False, "error": "PHOTOTAG_SCAN_ROOT nicht lesbar"}), 503
        try:
            validate_source(nc_path)
            jpeg, meta = blur_preview_for_nc(nc_path, opts_raw if isinstance(opts_raw, dict) else None)
        except NoFacesFound as exc:
            return jsonify({"ok": False, "error": str(exc), "no_faces": True}), 404
        except (FaceBlurError, FileNotFoundError, OSError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        resp = app.response_class(jpeg, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "private, no-store"
        if opt_errors:
            resp.headers["X-Phototag-Blur-Warnings"] = "; ".join(opt_errors)[:500]
        resp.headers["X-Phototag-Blur-Faces"] = str(meta.get("faces", 0))
        resp.headers["X-Phototag-Blur-Source"] = str(meta.get("settings_source", "env"))
        return resp

    @app.get("/api/blur/jobs/active")
    def api_blur_job_active():
        """The job that is running right now (so the UI can pick it up again after a page reload)."""
        _check_api_auth()
        from phototag.blur_jobs import active_job, get_blur_job
        from phototag.cli import connect

        con = connect()
        row = active_job(con)
        return jsonify({"ok": True, "job": get_blur_job(con, row["id"]) if row else None})

    @app.get("/api/blur/jobs/<job_id>")
    def api_blur_job_status(job_id: str):
        _check_api_auth()
        from phototag.blur_jobs import get_blur_job
        from phototag.cli import connect

        job = get_blur_job(connect(), job_id)
        if not job:
            return jsonify({"ok": False, "error": "Job nicht gefunden"}), 404
        return jsonify({"ok": True, "job": job})

    @app.post("/api/blur/<int:image_id>/approve")
    def api_blur_approve(image_id: int):
        _check_api_auth()
        from phototag.blur_jobs import approve_blur
        from phototag.cli import connect

        try:
            return jsonify(approve_blur(connect(), image_id))
        except (LookupError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.post("/api/blur/<int:image_id>/reject")
    def api_blur_reject(image_id: int):
        _check_api_auth()
        from phototag.blur_jobs import reject_blur
        from phototag.cli import connect

        try:
            return jsonify(reject_blur(connect(), image_id))
        except (LookupError, ValueError, FileNotFoundError, OSError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    # ------------------------------------------------ review of a video blur (timeline, corrections, re-render)

    def _review_error(exc: Exception):
        code = 404 if isinstance(exc, (LookupError, FileNotFoundError)) else 400
        return jsonify({"ok": False, "error": str(exc.args[0] if exc.args else exc)}), code

    @app.get("/api/blur/<int:image_id>/review")
    def api_blur_review(image_id: int):
        _check_api_auth()
        from phototag.blur_jobs import _blur_row
        from phototag.blur_review import review_payload
        from phototag.cli import connect

        con = connect()
        try:
            _blur_row(con, image_id)
            resp = jsonify(review_payload(con, image_id))
        except (LookupError, ValueError) as exc:
            return _review_error(exc)
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    @app.get("/api/blur/<int:image_id>/frame/<int:frame>")
    def api_blur_review_frame(image_id: int, frame: int):
        """One frame of the ORIGINAL video (unblurred, so the person can see what is there)."""
        _check_api_auth()
        from phototag.admin_mount import physical_file_path
        from phototag.blur_jobs import _blur_row
        from phototag.blur_review import frame_jpeg, load_analysis
        from phototag.cli import connect
        from phototag.faceblur import FaceBlurError
        from phototag.nextcloud_sync import nc_dav_relative_path

        con = connect()
        try:
            row = _blur_row(con, image_id)
            an, _opts, _r = load_analysis(con, image_id)
            src_row = con.execute("SELECT root, path FROM images WHERE id=?", (row["derived_from"],)).fetchone()
            if not src_row:
                raise LookupError("Original nicht mehr in der Datenbank")
            src = physical_file_path(nc_dav_relative_path(src_row["root"], src_row["path"]))
            if not src.is_file():
                raise FileNotFoundError("Original fehlt auf dem Mount")
            jpeg = frame_jpeg(src, max(0, min(frame, an.total_frames - 1)), an.fps, request.args.get("w", 640, type=int))
        except (LookupError, ValueError, FileNotFoundError, FaceBlurError) as exc:
            return _review_error(exc)
        resp = app.response_class(jpeg, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "private, max-age=300"      # same frame of the same original never changes
        return resp

    @app.post("/api/blur/<int:image_id>/edits")
    def api_blur_edit_add(image_id: int):
        _check_api_auth()
        from phototag.blur_jobs import _blur_row
        from phototag.blur_review import add_edit, load_analysis
        from phototag.cli import connect

        body = request.get_json(silent=True) or {}
        con = connect()
        try:
            _blur_row(con, image_id)
            an, _opts, _r = load_analysis(con, image_id)
            edit = add_edit(
                con, image_id, an, frame_from=body.get("frame_from"), frame_to=body.get("frame_to"),
                mode=str(body.get("mode") or ""), rect_start=body.get("rect_start"), rect_end=body.get("rect_end"),
                replace=body.get("replace", True) not in (False, 0, "0", "false"),
            )
        except (LookupError, ValueError) as exc:
            return _review_error(exc)
        return jsonify({"ok": True, "edit": edit.as_dict(), "pending": True})

    @app.delete("/api/blur/<int:image_id>/edits/<int:edit_id>")
    def api_blur_edit_delete(image_id: int, edit_id: int):
        _check_api_auth()
        from phototag.blur_jobs import _blur_row
        from phototag.blur_review import delete_edit
        from phototag.cli import connect

        con = connect()
        try:
            _blur_row(con, image_id)
        except (LookupError, ValueError) as exc:
            return _review_error(exc)
        if not delete_edit(con, image_id, edit_id):
            return jsonify({"ok": False, "error": "Korrektur nicht gefunden"}), 404
        return jsonify({"ok": True, "pending": True})

    @app.post("/api/blur/<int:image_id>/rerender")
    def api_blur_rerender(image_id: int):
        _check_api_auth()
        from phototag.blur_jobs import BlurBusy, create_rerender_job, get_blur_job, spawn_blur_job_subprocess
        from phototag.cli import connect

        if not mount_accessible():
            return jsonify({"ok": False, "error": "PHOTOTAG_SCAN_ROOT nicht lesbar"}), 503
        con = connect()
        try:
            job_id = create_rerender_job(con, image_id)
        except BlurBusy as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        except (LookupError, ValueError, FileNotFoundError) as exc:
            return _review_error(exc)
        spawn_blur_job_subprocess(job_id)
        return jsonify({"ok": True, "job": get_blur_job(con, job_id)})

    @app.get("/api/blur/<int:image_id>/photo")
    def api_blur_photo(image_id: int):
        """The original photo (view=original) or the blur copy (view=copy), turned and scaled, as JPEG."""
        _check_api_auth()
        from phototag.admin_mount import physical_file_path
        from phototag.blur_jobs import _blur_row
        from phototag.blur_photo import has_photo, photo_jpeg
        from phototag.cli import connect
        from phototag.faceblur import FaceBlurError
        from phototag.nextcloud_sync import nc_dav_relative_path

        con = connect()
        try:
            row = _blur_row(con, image_id)
            if not has_photo(con, image_id):
                raise LookupError("Für diese Kopie liegt keine Analyse vor — Blur neu erzeugen.")
            which = row if request.args.get("view") == "copy" else con.execute(
                "SELECT root, path FROM images WHERE id=?", (row["derived_from"],)).fetchone()
            if not which:
                raise LookupError("Original nicht mehr in der Datenbank")
            path = physical_file_path(nc_dav_relative_path(which["root"], which["path"]))
            if not path.is_file():
                raise FileNotFoundError("Datei fehlt auf dem Mount")
            jpeg = photo_jpeg(path, request.args.get("w", 1000, type=int))
        except (LookupError, ValueError, FileNotFoundError, FaceBlurError) as exc:
            return _review_error(exc)
        resp = app.response_class(jpeg, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    @app.put("/api/blur/<int:image_id>/strokes")
    def api_blur_strokes(image_id: int):
        _check_api_auth()
        from phototag.blur_jobs import _blur_row
        from phototag.blur_photo import set_strokes
        from phototag.cli import connect

        body = request.get_json(silent=True) or {}
        con = connect()
        try:
            _blur_row(con, image_id)
            result = set_strokes(con, image_id, body.get("strokes"))
        except (LookupError, ValueError) as exc:
            return _review_error(exc)
        return jsonify({"ok": True, **result})

    @app.post("/api/blur/<int:image_id>/photo-render")
    def api_blur_photo_render(image_id: int):
        _check_api_auth()
        from phototag.blur_photo import rerender_photo
        from phototag.cli import connect
        from phototag.faceblur import FaceBlurError

        if not mount_accessible():
            return jsonify({"ok": False, "error": "PHOTOTAG_SCAN_ROOT nicht lesbar"}), 503
        con = connect()
        try:
            return jsonify(rerender_photo(con, image_id))
        except (LookupError, ValueError, FileNotFoundError, FaceBlurError, OSError) as exc:
            return _review_error(exc)

    @app.get("/api/images/<int:image_id>/thumb")
    def api_thumb(image_id: int):
        _check_api_auth()
        from phototag.cli import connect

        con = connect()
        row = con.execute("SELECT thumb FROM images WHERE id=?", (image_id,)).fetchone()
        if not row or not row["thumb"]:
            abort(404)
        return _thumb_response(row["thumb"])

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
