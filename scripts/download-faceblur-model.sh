#!/usr/bin/env bash
# YuNet face detector (OpenCV Zoo, MIT licence) -> $PHOTOTAG_INSTALL_DIR/models/
set -euo pipefail

INSTALL_DIR="${PHOTOTAG_INSTALL_DIR:-/opt/phototag}"
DEST_DIR="$INSTALL_DIR/models"
NAME="face_detection_yunet_2023mar.onnx"
URL="https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/$NAME"
SHA256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"

mkdir -p "$DEST_DIR"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
if command -v wget >/dev/null; then wget -qO "$TMP" "$URL"; else curl -fsSL -o "$TMP" "$URL"; fi
GOT="$(sha256sum "$TMP" | cut -d' ' -f1)"
if [[ "$GOT" != "$SHA256" ]]; then
  echo "FEHLER: Pruefsumme stimmt nicht ($GOT) — Datei nicht installiert." >&2
  exit 1
fi
install -m 0644 "$TMP" "$DEST_DIR/$NAME"
echo "OK: $DEST_DIR/$NAME"
echo "In phototag.env eintragen:  PHOTOTAG_FACEBLUR_MODEL=$DEST_DIR/$NAME"
echo "Dann:  ./scripts/update-from-github.sh   (installiert opencv/numpy)  und  systemctl restart phototag-admin"
