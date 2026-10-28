# phototag — Agent notes

- Run tests before push: `pip install -e ".[dev]"` → `pytest -q`
- Never commit `categories.txt` — only `categories.example.txt` in git.
- **Public GitHub:** generic photo-tagging tool. **No** private taxonomy, tag definitions, NSFW examples, or homelab folder names in this repo. Taxonomy + sanitize rules: server-only files (`categories.txt`, optional `PHOTOTAG_SANITIZE_RULES`, `PHOTOTAG_REQUIRED_TAGS_FILE`); Homelab copies in private `doku/` / `tempdok/`.
- **Deploy:** keep `DEPLOY.md` + `CHANGELOG.md` current; new env keys → `.env.example`. `update-from-github.sh` must call `post-update.sh` (no tag text from repo).
