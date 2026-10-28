# phototag — Agent notes

- Run tests before push: `pip install -e ".[dev]"` → `pytest -q`
- Never commit `categories.txt` — only `categories.example.txt` in git.
- **Public GitHub:** generic photo-tagging tool. **No** private taxonomy, tag definitions, NSFW examples, homelab paths, or **sanitize/rule JSON files** in this repo — not even “example” JSON. Taxonomy + rules: server-only paths in `phototag.env`; masters in private `doku/` / `tempdok/` (deploy via copy from homelab, not from GitHub).
- **Deploy:** keep `DEPLOY.md` + `CHANGELOG.md` current; new env keys → `.env.example`. `update-from-github.sh` must call `post-update.sh` (no tag text from repo).
