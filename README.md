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

Release-Pin per **Git-Tag** (empfohlen) oder Commit-SHA:

```bash
apt install -y wget unzip rsync python3-venv ffmpeg
install -d /opt/phototag
export PHOTOTAG_REF=v0.3.0
# Privates GitHub-Repo:
# export GITHUB_TOKEN=<read-only PAT>
bash /opt/phototag/scripts/update-from-github.sh
cp categories.example.txt categories.txt && nano categories.txt
```

Alternativ nach `git clone`: `export PHOTOTAG_REF="$(git rev-parse HEAD)"`.

**Supply chain:** Updates nur mit gesetztem `PHOTOTAG_REF` / `PHOTOTAG_TAG` (Standard im Skript: `v0.3.0`). Unpinned `main` ist blockiert.

## Entwicklung

```bash
git clone git@github.com:lastphoenx/phototag.git
cd phototag
cp categories.example.txt categories.txt
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

`PHOTOTAG_*` werden zur Laufzeit gelesen (`phototag.config`).
