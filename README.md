# skill-manager

A local web UI for browsing, auditing, and toggling AI agent skills.

If you have a `~/.hermes/skills` directory with a hundred `SKILL.md` files, you eventually lose track of what is installed, what it costs per turn, and what you never use. This is a small FastAPI + vanilla JS dashboard that reads that directory and answers those questions.

No build step. No database. No framework on the frontend.

## What it does

- **Card grid** of every skill, filterable by source (hub / skills.sh / local / symlink / core), status, and whether it was modified locally
- **Detail drawer** with the file tree; `SKILL.md` renders as markdown, everything else shows with line numbers, toggle between rendered and source
- **Enable / disable** writes `skills.disabled` in your Hermes config, so a disabled skill stops being mounted
- **Batch operations** — select several skills, enable or disable them in one write
- **Context cost** — estimates how many tokens the enabled skills add to every turn's system prompt
- **Update** per skill, dispatched by source (Hermes hub uses its own content hash, skills.sh uses `npx skills`)
- **Description translation** — English descriptions translated to Chinese via the Google endpoint, with a per-card and per-drawer toggle to flip back to the original

## Requirements

- Python 3.11+ (uses `tomllib`)
- A skill directory laid out as `<category>/<name>/SKILL.md`, or `<name>/SKILL.md`
- Optional: Hermes installed at `~/.hermes` if you want enable/disable and upstream updates to work

## Quick start

```bash
git clone <this repo> skill-manager
cd skill-manager
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp config.example.toml config.toml   # optional, defaults work
python3 server.py
```

Open http://127.0.0.1:8900

No auth by design. It binds to `127.0.0.1` and expects you to put a reverse proxy with authentication in front if you ever expose it. The project does not care how you protect it.

## Configuration

`config.toml` next to `server.py`. Every key has a default, so the file is optional. See `config.example.toml` for the full list.

```toml
[paths]
skill_roots = ["~/.hermes/skills"]     # multiple roots allowed, deduped by realpath
hermes_config = "~/.hermes/config.yaml"

[translate]
proxy = ""                              # set if translate.googleapis.com is unreachable
```

Environment variables override the file. `SKILLMGR_SERVER__PORT=9000` sets `server.port`. Single underscore for the section, double underscore to reach a nested key.

## How it reads your skills

Four things get computed per skill, and each has a caveat worth knowing:

**Size** counts every file under the skill directory, not just `SKILL.md`. A skill with a 15 MB asset directory is genuinely 15 MB on disk.

**Context tokens** estimates what the skill costs per turn. Only the `name: description` line is injected into the system prompt, not the body. The estimator counts CJK characters at ~1 token each and everything else at ~3.5 chars per token. A pure `len()/4` formula underestimates mixed Chinese/English descriptions badly — on a real 111-skill library the two formulas differ by 40%.

**Call count** merges two sources and takes the maximum: the `use_count` field in `.usage.json` (written lazily, sometimes hours late) and `skill_view` tool calls parsed out of `state.db` (written immediately). This counts *explicit* lookups. A skill that gets loaded automatically by the index leaves no trace, so a low number does not mean the skill is useless.

**Source** is resolved in order: Hermes hub lock, then skills.sh lock, then a local `data/baseline.json`, then "local". Skills that are symlinked into place report both the link path and the real path.

## Translation

Uses the free `translate.googleapis.com` endpoint — no API key, no LLM. Two things make it usable:

Technical terms are swapped for `[[#N#]]` placeholders before sending and restored after. Without this, `Mermaid` comes back as "美人鱼" and `HeyGen` as "嘿根". Private-use Unicode characters were tried first; Google strips them and leaves the digits behind.

Translations cache to `data/desc_zh.json`, keyed by skill name, with the source text's hash stored alongside. Edit a `SKILL.md` description and the stale entry stops matching, so the UI shows the original English again until you retranslate. Deleted skills get pruned from the cache on the next translation run.

## API

All endpoints are read-only except the three POST routes.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/skills` | list + summary; `?refresh=true` bypasses the 30s cache |
| GET | `/api/skills/{name}` | one skill with its file tree |
| GET | `/api/skills/{name}/file?path=` | file contents or binary metadata |
| POST | `/api/skills/{name}/toggle` | `{"enabled": bool}` |
| POST | `/api/skills/batch-toggle` | `{"names": [...], "enabled": bool}` |
| POST | `/api/skills/{name}/update` | pull from upstream |
| POST | `/api/skills/translate` | `{"names": [...]}` or `{}` for the next batch |
| GET | `/api/me` | version info |
| GET | `/healthz` | liveness |

## Project layout

```
server.py           FastAPI routes, 30s response cache
core/config.py      config.toml + env override
core/scan.py        directory walk, frontmatter parsing, token estimate
core/sources.py     upstream lock files, integrity check
core/toggle.py      writes skills.disabled, audit log
core/update.py      per-source update dispatch
core/translate.py   Google endpoint, term protection, cache
static/             index.html, app.js, style.css, vendored marked.js
```

## Known limitations

- **Frontmatter parsing needs `pyyaml`.** The fallback line-based parser cannot handle `description: >` folded blocks and will silently report an empty description.
- **`npx skills check` is not a dry run.** It updates immediately. The update path therefore never calls `check`, and relies on comparing against `data/baseline.json` instead.
- **Local modifications block updates.** If a skill's content hash no longer matches upstream, the update button is disabled rather than silently overwriting your edits.
- **Disabling takes effect next turn**, not instantly — the system prompt is built per turn.
- **The call count is a lower bound.** See the note above about automatic skill loading.

## License

MIT
