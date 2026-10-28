# Deploy phototag (CT / Server)

Standard-Pfad: **`/opt/phototag`**, Konfiguration **`phototag.env`** (chmod 600), DB und `categories.txt` bleiben beim Update erhalten.

## 1. Code aktualisieren

```bash
cd /opt/phototag
./scripts/update-from-github.sh
```

Das Skript:

- zieht **GitHub `main`**
- `pip install -e .`
- ruft **`./scripts/post-update.sh`** auf (Checkliste unten)

Falls das Update-Skript sehr alt ist: `./scripts/bootstrap-update-from-main.sh` (einmalig).

## 2. Pflicht nach jedem Update

```bash
cd /opt/phototag
./scripts/post-update.sh          # zeigt fehlende Env-Keys + Category-Tags
./scripts/sync-phototag-env.sh --apply   # fehlende Keys aus .env.example an phototag.env (kommentiert)
nano phototag.env                 # neue Zeilen aktivieren / Werte setzen
```

**`.env.example`** im Repo ist die **Referenz aller Keys** — `phototag.env` wird beim rsync **nicht** überschrieben. Neue Versionen fügen Keys hinzu; ohne `sync-phototag-env.sh` fehlen dir Features still (Nacht-Lauf, Sanitize, …).

## 3. categories.txt (privat)

- Liegt nur auf dem Server, **nicht** im GitHub-Repo.
- `post-update.sh` prüft **`scripts/required-category-tags.txt`**.
- Fehlende Zeilen: Text aus **`scripts/category-tag-snippets.txt`** in deine `categories.txt` kopieren (oder aus deinem privaten Master).

## 4. Verifizieren

```bash
set -a && source phototag.env && set +a
.venv/bin/phototag version
.venv/bin/phototag stats
```

Erster Tag-Test: kleines Limit, `-v` optional.

## 5. Nacht-Lauf (optional)

1. `PHOTOTAG_SCAN_ROOT` in `phototag.env` (Mount = gleiche Pfade wie NC `OF/…`).
2. `scripts/systemd/phototag-night.{service,timer}` nach `/etc/systemd/system/` kopieren, Pfade anpassen.
3. `systemctl enable --now phototag-night.timer`

Details: README Abschnitt „Nacht-Lauf“.

## 6. Release-Notizen

Pro Version: **`CHANGELOG.md`** — was sich geändert hat und ob du Env/Categories nachziehen musst.

## Was du **nicht** committen sollst

`phototag.env`, `categories.txt`, `state/*.db`, `secrets/` — siehe `.gitignore` und `SECURITY.md`.
