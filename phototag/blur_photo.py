"""Brush corrections for PHOTO blur copies (the video counterpart is blur_review).

The blur job keeps the detected faces. A person paints over the ORIGINAL in the admin: ``add`` strokes blur more,
``erase`` strokes take the blur away again. The copy is then rendered anew from the original: automatic blur of
the stored faces, then the strokes in the order they were painted.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from phototag.blur_review import _face_from_list, _face_to_list
from phototag.config import faceblur_margin
from phototag.faceblur import (
    BLUR_OK,
    BLUR_REVIEW,
    FaceBlurError,
    _require_cv2,
    _shape_poly,
    apply_blur,
    blur_frame,
    boxes_for_face,
    write_blur_image,
)

OPS = ("add", "erase")
MAX_STROKES = 400
MAX_POINTS = 4000
MIN_SIZE, MAX_SIZE = 0.004, 0.30          # brush diameter as a share of the image width

DDL = """
CREATE TABLE IF NOT EXISTS blur_photo(
  image_id INTEGER PRIMARY KEY,           -- the blur copy
  faces TEXT NOT NULL,                    -- JSON list of detected faces
  size TEXT NOT NULL,                     -- JSON [width, height] of the (EXIF-turned) original
  options TEXT,                           -- UI options of the job (JSON) or NULL
  strokes TEXT NOT NULL DEFAULT '[]',
  rev INTEGER NOT NULL DEFAULT 0,         -- changes with every saved stroke list
  rendered_rev INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);
"""


def ensure_table(con: sqlite3.Connection) -> None:
    con.executescript(DDL)
    con.commit()


def save_photo_analysis(con: sqlite3.Connection, image_id: int, analysis: Any, options: dict[str, Any] | None) -> None:
    ensure_table(con)
    faces = json.dumps([_face_to_list(0, f) for f in analysis.faces], separators=(",", ":"))
    con.execute(
        "INSERT OR REPLACE INTO blur_photo(image_id,faces,size,options,strokes,rev,rendered_rev,created_at) "
        "VALUES(?,?,?,?,'[]',0,0,?)",
        (image_id, faces, json.dumps([analysis.width, analysis.height]),
         json.dumps(options, ensure_ascii=False) if options else None, time.time()),
    )
    con.commit()


def has_photo(con: sqlite3.Connection, image_id: int) -> bool:
    ensure_table(con)
    return con.execute("SELECT 1 FROM blur_photo WHERE image_id=?", (image_id,)).fetchone() is not None


def load_photo(con: sqlite3.Connection, image_id: int) -> dict[str, Any]:
    ensure_table(con)
    row = con.execute("SELECT * FROM blur_photo WHERE image_id=?", (image_id,)).fetchone()
    if not row:
        raise LookupError("Für diese Kopie liegt keine Analyse vor (älter als die Prüf-Funktion) — Blur neu erzeugen.")
    return {
        "faces": [_face_from_list(r)[1] for r in json.loads(row["faces"])],
        "size": tuple(json.loads(row["size"])),
        "options": json.loads(row["options"]) if row["options"] else None,
        "strokes": json.loads(row["strokes"]),
        "rev": int(row["rev"]),
        "rendered_rev": int(row["rendered_rev"]),
    }


def delete_photo_data(con: sqlite3.Connection, image_id: int) -> None:
    ensure_table(con)
    con.execute("DELETE FROM blur_photo WHERE image_id=?", (image_id,))
    con.commit()


def clean_strokes(raw: Any) -> list[dict[str, Any]]:
    """Validate what the browser sent: [{op, size, pts:[[x,y],...]}], all coordinates as 0..1 shares."""
    if not isinstance(raw, list):
        raise ValueError("strokes muss eine Liste sein")
    if len(raw) > MAX_STROKES:
        raise ValueError(f"Zu viele Striche (höchstens {MAX_STROKES})")
    out = []
    for s in raw:
        if not isinstance(s, dict) or s.get("op") not in OPS:
            raise ValueError("Strich ohne gültige Art (add/erase)")
        pts = s.get("pts")
        if not isinstance(pts, list) or not pts or len(pts) > MAX_POINTS:
            raise ValueError("Strich ohne Punkte oder mit zu vielen Punkten")
        try:
            size = max(MIN_SIZE, min(MAX_SIZE, float(s.get("size"))))
            clean = [[max(0.0, min(1.0, float(p[0]))), max(0.0, min(1.0, float(p[1])))] for p in pts]
        except (TypeError, ValueError, IndexError):
            raise ValueError("Strich enthält ungültige Zahlen") from None
        out.append({"op": s["op"], "size": round(size, 4), "pts": [[round(x, 4), round(y, 4)] for x, y in clean]})
    return out


def set_strokes(con: sqlite3.Connection, image_id: int, raw: Any) -> dict[str, Any]:
    load_photo(con, image_id)
    strokes = clean_strokes(raw)
    con.execute(
        "UPDATE blur_photo SET strokes=?, rev=rev+1 WHERE image_id=?",
        (json.dumps(strokes, separators=(",", ":")), image_id),
    )
    con.commit()
    p = load_photo(con, image_id)
    return {"strokes": len(strokes), "rev": p["rev"], "pending": p["rev"] != p["rendered_rev"]}


# ---------------------------------------------------------------- rendering

def _stroke_mask(stroke: dict[str, Any], w: int, h: int):
    """(x0, y0, x1, y1, mask 0..1 float32) for the box around one stroke."""
    cv2, np = _require_cv2()
    d = max(2, int(round(stroke["size"] * w)))
    pts = [(int(round(x * (w - 1))), int(round(y * (h - 1)))) for x, y in stroke["pts"]]
    pad = d + 4
    x0, y0 = max(0, min(p[0] for p in pts) - pad), max(0, min(p[1] for p in pts) - pad)
    x1, y1 = min(w, max(p[0] for p in pts) + pad + 1), min(h, max(p[1] for p in pts) + pad + 1)
    m = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8)
    local = [(px - x0, py - y0) for px, py in pts]
    if len(local) > 1:
        cv2.polylines(m, [np.array(local, dtype=np.int32)], False, 255, thickness=d, lineType=cv2.LINE_AA)
    for p in (local[0], local[-1]):
        cv2.circle(m, p, d // 2, 255, -1, lineType=cv2.LINE_AA)
    sigma = max(0.5, d * 0.08)                         # soft edge, no hard seam against the surrounding blur
    m = cv2.GaussianBlur(m, (0, 0), sigma)
    return x0, y0, x1, y1, m.astype(np.float32) / 255.0


def render_array(original_bgr, faces: list, strokes: list[dict[str, Any]]):
    """Automatic blur of the stored faces, then every stroke on top (add = blurred original, erase = original)."""
    _, np = _require_cv2()
    h, w = original_bgr.shape[:2]
    out = original_bgr.copy()
    blur_frame(out, faces, margin=faceblur_margin())
    strong = None
    for s in strokes:
        x0, y0, x1, y1, m = _stroke_mask(s, w, h)
        if s["op"] == "add":
            if strong is None:
                strong = original_bgr.copy()
                apply_blur(strong, [(0, 0, w, h)])
            src = strong[y0:y1, x0:x1]
        else:
            src = original_bgr[y0:y1, x0:x1]
        roi = out[y0:y1, x0:x1].astype(np.float32)
        out[y0:y1, x0:x1] = (roi * (1.0 - m[..., None]) + src.astype(np.float32) * m[..., None]).astype(np.uint8)
    return out


def _load_original(src: Path):
    """(bgr, alpha or None) — EXIF turned, like the blur job saw it."""
    _, np = _require_cv2()
    from PIL import Image, ImageOps

    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
            alpha = im.convert("RGBA").getchannel("A") if has_alpha else None
            rgb = np.asarray(im.convert("RGB"))
    except OSError as exc:
        raise FaceBlurError(f"Bild nicht lesbar: {exc}") from exc
    return np.ascontiguousarray(rgb[:, :, ::-1]), alpha


def rerender_photo(con: sqlite3.Connection, image_id: int) -> dict[str, Any]:
    """Overwrite the blur copy with a fresh render (synchronous: a photo takes a second or two)."""
    from phototag.admin_mount import physical_file_path
    from phototag.blur_jobs import _blur_row, register_blurred_file
    from phototag.blur_options import temporary_blur_overlay
    from phototag.config import faceblur_settings_summary, faceblur_suffix, load_phototag_env
    from phototag.faceblur import is_blurred_name, sweep_stale_blur_temps, temp_output_path
    from phototag.nextcloud_sync import nc_dav_relative_path

    load_phototag_env(override=True)
    row = _blur_row(con, image_id)
    if row["status"] not in (BLUR_REVIEW, BLUR_OK):
        raise ValueError(f"id={image_id} status={row['status']}: nur Kopien in Prüfung")
    p = load_photo(con, image_id)
    src_row = con.execute("SELECT * FROM images WHERE id=?", (row["derived_from"],)).fetchone()
    if not src_row:
        raise LookupError("Original nicht mehr in der Datenbank")
    src = physical_file_path(nc_dav_relative_path(src_row["root"], src_row["path"]))
    if not src.is_file():
        raise FileNotFoundError(f"Original fehlt auf dem Mount: {src.name}")
    dest_nc = nc_dav_relative_path(row["root"], row["path"])
    dest = physical_file_path(dest_nc)
    if not is_blurred_name(dest.name, faceblur_suffix()):
        raise ValueError("Dateiname enthält nicht das Blur-Suffix — nicht überschrieben")
    bgr, alpha = _load_original(src)
    if (bgr.shape[1], bgr.shape[0]) != tuple(p["size"]):
        raise FaceBlurError("Das Original hat sich seit dem Blur geändert — Blur neu erzeugen")
    sweep_stale_blur_temps()
    tmp = temp_output_path(dest.suffix)
    try:
        with temporary_blur_overlay(p["options"]):           # region/margin/strength the copy was made with
            out = render_array(bgr, p["faces"], p["strokes"])
            summary = faceblur_settings_summary()
        write_blur_image(out, tmp, alpha)
        shutil.copyfile(tmp, dest)
        if dest.stat().st_size != tmp.stat().st_size:
            raise FaceBlurError("Kopie auf den Mount unvollständig (Grösse weicht ab)")
        register_blurred_file(con, dest_nc, source_id=src_row["id"], source_name=src.name, faces=len(p["faces"]),
                              info=f"{summary}; nachbearbeitet: {len(p['strokes'])} Strich(e)", local=tmp)
        con.execute("UPDATE blur_photo SET rendered_rev=? WHERE image_id=?", (p["rev"], image_id))
        con.commit()
    finally:
        tmp.unlink(missing_ok=True)
    return {"ok": True, "id": image_id, "nc_path": dest_nc, "status": BLUR_REVIEW, "strokes": len(p["strokes"])}


# ---------------------------------------------------------------- what the review shows

def photo_payload(con: sqlite3.Connection, image_id: int) -> dict[str, Any]:
    from phototag.blur_options import temporary_blur_overlay

    p = load_photo(con, image_id)
    w, h = p["size"]
    shapes: list[list[list[float]]] = []
    with temporary_blur_overlay(p["options"]):
        margin = faceblur_margin()
        for f in p["faces"]:
            for sh in boxes_for_face(f, None, margin, w, h):
                shapes.append([[round(x / w, 4), round(y / h, 4)] for x, y in _shape_poly(sh)])
    return {
        "ok": True, "kind": "image", "id": image_id, "width": w, "height": h, "aspect": w / float(h),
        "shapes": shapes, "strokes": p["strokes"], "rev": p["rev"], "pending": p["rev"] != p["rendered_rev"],
    }


def photo_jpeg(path: Path, width: int, quality: int = 85) -> bytes:
    """The image (EXIF turned) as a JPEG at most ``width`` wide — original or the blurred copy."""
    cv2, _ = _require_cv2()
    bgr, _alpha = _load_original(path)
    width = max(200, min(2400, int(width)))
    h, w = bgr.shape[:2]
    if w > width:
        bgr = cv2.resize(bgr, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise FaceBlurError("Bild konnte nicht kodiert werden")
    return buf.tobytes()
