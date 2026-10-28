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
./scripts/post-update.sh
./scripts/sync-phototag-env.sh              # fehlende *aktive* Keys
./scripts/sync-phototag-env.sh --catalog    # alle optionalen Keys aus .env.example
diff -u docs/phototag-env.reference.txt phototag.env  # grober Soll-Ist-Vergleich
nano phototag.env
```

**`.env.example`** im Repo ist die **Referenz aller Keys** — `phototag.env` wird beim rsync **nicht** überschrieben. Neue Versionen fügen Keys hinzu; `sync-phototag-env.sh` vergleicht nur **aktive** Zeilen in `.env.example` (ohne `#`). Kommentierte Optionen dort sind Doku — kein Muss. **Nextcloud:** `PHOTOTAG_NC_TOKEN_FILE` **oder** `PHOTOTAG_NC_APP_PASSWORD` reicht.

## 3. categories.txt (privat)

- Liegt nur auf dem Server, **nicht** im GitHub-Repo (`categories.example.txt` ist nur Demo).
- `post-update.sh` zählt Tag-Zeilen — **keine** Tag-Inhalte aus dem Repo.
- Optional in **`phototag.env`:** `PHOTOTAG_REQUIRED_TAGS_FILE` (eine Tag-ID pro Zeile; nur Server).
- Tag-Widersprüche: **`PHOTOTAG_SANITIZE_RULES`** → JSON (Schema: `docs/sanitize-rules.example.json`). Homelab-Regeln: privates `doku/phototag/sanitize-rules.json` nach `/opt/phototag/local/` kopieren.

## 4. Verifizieren

```bash
set -a && source phototag.env && set +a
.venv/bin/phototag version
.venv/bin/phototag stats
```

### push-nc testen (Schlagworte zurück nach Nextcloud)

Voraussetzung: Datei in der DB muss **unter demselben relativen Pfad** in NC existieren wie `PHOTOTAG_NC_DAV_PREFIX` — z. B. DB-Pfad `2024/foo.jpg` → NC `…/files/USER/Photos/2024/foo.jpg` (Prefix = dein NC-Unterordner).

1. **Dry-run:** `phototag push-nc --dry-run --limit 5` — zeigt, welche Pfade versucht würden.
2. **Echter Push:** `phototag push-nc --limit 5` — bei Fehler `PROPFIND … HTTP 404` existiert die Datei in NC nicht (lokaler Test-Ordner ≠ NC, unless dieselben Dateien auch unter dem DAV-Prefix liegen).
3. **Erneut pushen:** `--force` (ignoriert `nc_pushed`-Cache).

Erster Tag-Test: kleines Limit, `-v` optional.

## 5. Nacht-Lauf (optional, noch manuell aktivieren)

**Im Repo:** nur Vorlagen — **auf CT 142 ist kein systemd-Timer automatisch installiert**, bis du `cp` + `enable` ausführst. Prüfen: `systemctl status phototag-night.timer` (wenn „not found“ → noch nicht eingerichtet).

### Was `PHOTOTAG_SCAN_ROOT` ist

Mount- oder Cache-Pfad auf dem CT, dessen **relative Pfade** zu Nextcloud passen:

- **Remote-Name** (rclone o. Ä.) ≠ Mount-Pfad.
- **`PHOTOTAG_NC_DAV_PREFIX`** = Unterordner unter `…/dav/files/USER/` für WebDAV/push-nc (z. B. `Photos`).
- **`PHOTOTAG_SCAN_ROOT`** = Wurzel für `phototag scan`; DB speichert Pfade relativ dazu (z. B. `foo.jpg` → NC `…/USER/Photos/foo.jpg` wenn Prefix `Photos` und Scan-Root den gleichen Baum zeigt).

Test: kleiner lokaler Ordner mit Kopien. Produktion: dein Mount — **Pfad in `phototag.env` eintragen**.

### Batch + Pause (15 / 60 ist nicht Pflicht)

| Variable | Default im Skript | Zweck |
|----------|-------------------|--------|
| `PHOTOTAG_NIGHT_BATCH` | 20 | Max. Dateien **pro** `phototag tag`-Aufruf (Ollama entlasten, Abbruch um 07:00 feiner) |
| `PHOTOTAG_NIGHT_SLEEP` | 45 | Sekunden **zwischen** Runden (scan → tag → warten) — GPU/CPU für andere Dienste |
| `PHOTOTAG_NIGHT_END_HOUR` | 7 | Schleife endet ab 07:00 |

Kleiner Bestand, schnelle Nacht: z. B. `BATCH=50`, `SLEEP=10`. Volle Bibliothek, vorsichtig: `BATCH=10`, `SLEEP=90`. Alles in **`phototag.env`**.

### Bilder und Videos

- **`scan`** nimmt **Bilder und Videos** (ffmpeg nötig).
- **`tag` ohne Option** verarbeitet **beides** gemischt (Reihenfolge DB).
- **`PHOTOTAG_NIGHT_IMAGES_FIRST=1`** in `phototag.env`: erst alle **`new`/`error` mit `kind=image`**, danach Rest (Videos + offene Fehler).
- **`PHOTOTAG_NIGHT_KIND=image`** oder **`video`**: nur diese Art (dauerhaft in der Nacht).

### systemd einrichten (einmalig, du auf dem CT)

```bash
cp /opt/phototag/scripts/systemd/phototag-night.service /etc/systemd/system/
cp /opt/phototag/scripts/systemd/phototag-night.timer /etc/systemd/system/
# phototag.env: PHOTOTAG_SCAN_ROOT, optional NIGHT_* setzen
systemctl daemon-reload
systemctl enable --now phototag-night.timer
systemctl list-timers | grep phototag
```

Timer startet **täglich 00:00**, Service läuft bis **`PHOTOTAG_NIGHT_END_HOUR`** (Default 7). Homelab-Detail: **`doku/phototag/nacht-tagging.md`** (privates Repo).

## 6. Release-Notizen

Pro Version: **`CHANGELOG.md`** — was sich geändert hat und ob du Env/Categories nachziehen musst.

## Was du **nicht** committen sollst

`phototag.env`, `categories.txt`, `state/*.db`, `secrets/` — siehe `.gitignore` und `SECURITY.md`.
