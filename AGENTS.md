# phototag — Agent notes

- Run tests before push: `pip install -e ".[dev]"` → `pytest -q`
- Never commit `categories.txt` — only `categories.example.txt` in git.
- **Public GitHub:** generic photo-tagging tool. No personal taxonomy, tag definitions, server paths or rule/prompt JSON files in this repo. Those live server-side (`PHOTOTAG_CATEGORIES`, `PHOTOTAG_TAGGING_CUSTOM`, `PHOTOTAG_SANITIZE_RULES` in `phototag.env`).
- **Deploy:** keep `DEPLOY.md` + `CHANGELOG.md` current; new env keys → `.env.example`. `update-from-github.sh` must call `post-update.sh`.
