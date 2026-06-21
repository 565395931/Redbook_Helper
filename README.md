# Redbook Analisyze

Local Xiaohongshu operations workspace for account isolation, competitor research, copy scoring, draft generation, image reference analysis, and exact duplicate highlighting. Crawling is only one data-source module.

## Start

Double-click `start.bat`, or run:

```powershell
cd D:\Redbook_helper\redbook_analisyze
uv run --python 3.11 uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Open http://127.0.0.1:8765.

If `uv` reports that `.venv\Scripts\python.exe` cannot be spawned, rebuild the local virtual environment once:

```powershell
Rename-Item .venv .venv.broken
uv run --python 3.11 python --version
```

## Bundled References

- `vendor\xhs_ai_publisher`: reference project, not committed to this repo.
- `vendor\Spider_XHS`: minimal crawler runtime subset required by the app. Local `.env`, `node_modules`, caches, and downloaded data are intentionally not committed.

If the vendor folder is missing or you want to restore optional reference projects on a fresh machine:

```powershell
.\scripts\setup_vendor.ps1 -IncludeReferencePublisher
```

If you have a locally modified `Spider_XHS`, place it at `vendor\Spider_XHS`.

## Current Product Modules

- Account login-state management and current-account switching.
- Brand profile with minimal optional fields.
- Competitor library and note crawling through `vendor\Spider_XHS`.
- Content workshop with scoring, sorting, summary, and draft generation.
- Image workshop placeholder for future multimodal analysis and generation API.
- Review flow where approved/published copy enters the exact-match dedupe memory.

## Login State

Current MVP supports manual Cookie login-state paste. In short: log in to Xiaohongshu in Chrome, open DevTools, search `edith.xiaohongshu`, copy the `cookie` request header, and paste it into the login form.

The next login iteration should mimic `xhs_ai_publisher`: visible browser login plus user-triggered Chrome login-state import. The app must not crawl until a login state exists.
