# phototag

Lokale **Foto- und Video-Verschlagwortung** mit einem Vision-Modell (OpenAI-kompatible `/v1/chat/completions`) und **SQLite**.

Für **private Medienarchive**: Dateien bleiben bei dir; Pixel gehen nur an deine eigene Vision-API (z. B. Ollama im LAN), nicht an Cloud-Vision-Dienste.

Bei Archiven **mit Personenfotos** optional eine Review-Queue: unsichere `age_status`-Einschätzung → Status `review` (keine Tags bis zur manuellen Freigabe).

## Kategorien

| Datei | Im Git |
|--------|--------|
| `categories.example.txt` | ja (neutrale Demo) |
| `categories.txt` | **nein** — `.gitignore` |

Eigene Taxonomie nur lokal oder per `PHOTOTAG_CATEGORIES`.

## Installation (Server)

Release-Pin optional per **Commit-SHA** oder Git-Tag; Standard auf dem Server: **`main`**:

```bash
apt install -y wget unzip rsync python3-venv ffmpeg
install -d /opt/phototag
bash /opt/phototag/scripts/update-from-github.sh
cp categories.example.txt categories.txt && nano categories.txt
```

Update (zieht **main** von GitHub, lässt `categories.txt` / `phototag.env` / DB):

```bash
./scripts/update-from-github.sh
.venv/bin/phototag version
```

Optional fixieren: `PHOTOTAG_UPDATE_REF=<commit-sha>` oder Tag (nur fuer `update-from-github.sh`; nicht `PHOTOTAG_REF` in `phototag.env` — das wuerde sonst jedes Update pinnen, wenn du `source phototag.env` nutzt).

### Nacht-Lauf (00:00–07:00)

`scripts/night-tag-loop.sh` scannt `PHOTOTAG_SCAN_ROOT` wiederholt und taggt in kleinen Batches (`PHOTOTAG_NIGHT_BATCH`, Pause `PHOTOTAG_NIGHT_SLEEP`). Stoppt ab `PHOTOTAG_NIGHT_END_HOUR` (Default 7). Systemd-Vorlage: `scripts/systemd/phototag-night.{service,timer}` — `PHOTOTAG_SCAN_ROOT` an euren OF-Mount anpassen.

**Ollama (gmk-evo):** nachts nur phototag → ein Modell reicht; tagsüber `OLLAMA_MAX_LOADED_MODELS=2` falls ein zweites Modell parallel soll. Kein Dauer-Parallelismus in phototag (serielles `tag`).

**Tag-Nachbearbeitung:** `PHOTOTAG_TAG_SANITIZE=1` (Default) — widersprüchliche Tags bereinigen; heikle Kombinationen → Status `review` (siehe `phototag review`).

## Entwicklung

```bash
git clone git@github.com:lastphoenx/phototag.git
cd phototag
cp categories.example.txt categories.txt
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

`PHOTOTAG_*` werden zur Laufzeit gelesen (`phototag.config`).
