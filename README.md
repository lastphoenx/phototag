# phototag

Lokale **Foto- und Video-Verschlagwortung** mit einem Vision-Modell (OpenAI-kompatible `/v1/chat/completions`) und **SQLite**.

Dateien bleiben bei dir; Pixel gehen nur an deine konfigurierte Vision-API (z. B. Ollama im LAN), nicht an Cloud-Vision-Dienste.

Optional Review-Queue bei unsicherer `age_status`-Einschätzung → Status `review`.

## Kategorien

| Datei | Im Git |
|--------|--------|
| `categories.example.txt` | ja (neutrale Demo) |
| `categories.txt` | **nein** — `.gitignore` |

Eigene Taxonomie nur lokal oder per `PHOTOTAG_CATEGORIES`.

## Installation (Server)

**Deploy-Runbook:** [`DEPLOY.md`](DEPLOY.md) · Release-Notizen: [`CHANGELOG.md`](CHANGELOG.md)

```bash
cd /opt/phototag
./scripts/update-from-github.sh    # ruft post-update.sh auf
./scripts/sync-phototag-env.sh --apply && nano phototag.env
```

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

Ausführlich in **[DEPLOY.md §5](DEPLOY.md)** (Batch/Sleep, Videos, Scan-Root, systemd **Vorlage only**). Kurz: `PHOTOTAG_SCAN_ROOT` in `phototag.env`, optional `PHOTOTAG_NIGHT_IMAGES_FIRST=1`.

## Entwicklung

```bash
git clone git@github.com:lastphoenx/phototag.git
cd phototag
cp categories.example.txt categories.txt
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

`PHOTOTAG_*` werden zur Laufzeit gelesen (`phototag.config`).
