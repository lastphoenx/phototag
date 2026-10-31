"""Measure the video face blur against frames a human labelled, so settings are tuned on evidence.

1. ``phototag faceblur-eval <video> --sheet`` writes a contact sheet (every STEP-th frame, numbered) to label from.
2. Write a small JSON file: ``{"frames": {"90": "full", "360": "part", "600": "none"}}`` -
   ``full`` = whole face visible, ``part`` = face cut off by the frame edge or mostly turned away,
   ``none`` = no face in the picture. Frames not listed are ignored.
3. ``phototag faceblur-eval <video> --truth labels.json`` runs the analysis and prints the hit rate and the false blocks.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from phototag.faceblur import FaceBlurError, FaceDetector, _require_cv2, probe_display
from phototag.faceblur_video import _RawFrames, analyze_video, tracks_to_frames

LABELS = ("full", "part", "none")


def load_truth(path: Path) -> dict[int, str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = data.get("frames", data) if isinstance(data, dict) else {}
    truth: dict[int, str] = {}
    for key, label in raw.items():
        if label not in LABELS:
            raise ValueError(f"Frame {key}: Label '{label}' unbekannt (erlaubt: {', '.join(LABELS)})")
        truth[int(key)] = label
    if not truth:
        raise ValueError("Keine Frames in der Referenzdatei")
    return truth


def score_coverage(truth: dict[int, str], covered: set[int]) -> dict[str, Any]:
    """Compare the frames that get blur with the labels."""
    def split(label: str) -> list[int]:
        return sorted(i for i, v in truth.items() if v == label)

    full, part, none = split("full"), split("part"), split("none")
    return {
        "full_total": len(full), "full_hit": sum(1 for i in full if i in covered),
        "part_total": len(part), "part_hit": sum(1 for i in part if i in covered),
        "none_total": len(none), "false_blocks": [i for i in none if i in covered],
        "missed_full": [i for i in full if i not in covered],
        "missed_part": [i for i in part if i not in covered],
    }


def format_report(r: dict[str, Any]) -> str:
    return "\n".join([
        f"volle Gesichter:        {r['full_hit']}/{r['full_total']}",
        f"abgeschnittene/halbe:   {r['part_hit']}/{r['part_total']}",
        f"Block ohne Gesicht:     {len(r['false_blocks'])}/{r['none_total']}  {r['false_blocks']}",
        f"verpasst (voll):        {r['missed_full']}",
        f"verpasst (teilweise):   {r['missed_part']}",
    ])


def evaluate(src: Path, truth_path: Path, detector: FaceDetector | None = None) -> str:
    truth = load_truth(truth_path)
    an = analyze_video(src, detector or FaceDetector())
    covered = set(tracks_to_frames(an.tracks, an.total_frames, an.fps, cuts=an.cuts))
    head = f"{src.name}: {an.total_frames} Frames, {len(an.tracks)} Gesicht(er), {len(an.cuts)} Schnitt(e), Blur auf {len(covered)} Frames"
    return head + "\n" + format_report(score_coverage(truth, covered))


def contact_sheet(src: Path, out_png: Path, *, step: int = 90, columns: int = 6, tile: int = 300) -> str:
    """Every ``step``-th frame, numbered, to label by eye."""
    import time

    cv2, np = _require_cv2()
    w, h, fps, _dur = probe_display(src)
    th = max(2, int(tile * h / w) // 2 * 2)
    reader = _RawFrames(src, w, h, scale_to=(tile, th), deadline=time.time() + 3600, fps=fps)
    tiles = []
    try:
        for i, frame in enumerate(reader):
            if i % step == 0:
                t = frame.copy()
                cv2.putText(t, str(i), (5, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)
                tiles.append(t)
    finally:
        reader.close()
    if not tiles:
        raise FaceBlurError("Keine Frames gelesen")
    while len(tiles) % columns:
        tiles.append(np.zeros_like(tiles[0]))
    sheet = np.vstack([np.hstack(tiles[k:k + columns]) for k in range(0, len(tiles), columns)])
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_png), sheet)
    return f"{out_png}  ({len(tiles)} Kacheln, alle {step} Frames)"
