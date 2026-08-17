# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Competitor Intelligence Pipeline — scrapes a competitor's website, pricing pages, social profiles and review snippets, then sends everything to an LLM for a structured analysis. Flask backend + single-file dashboard, results persisted as JSON files.

The domain is Ukrainian ISP/telecom competitors: scraper regexes, prompts, LLM output and all UI strings are Ukrainian (prices in UAH, `грн`/`₴` patterns). Keep new user-facing strings and prompt text in Ukrainian to match.

## Commands

```bash
pip install -r requirements.txt

export GROQ_API_KEY="gsk_..."       # required — see "API key" below
python server.py                     # dashboard + API on http://localhost:5000 (PORT env overrides)

python pipeline.py https://lanet.ua                 # full pipeline, CLI, prints progress + saves JSON
python scrapers/web_scraper.py https://ukrtelecom.ua # scraper only, dumps ScrapeResult JSON
python -m scrapers.social_scraper                    # telegram scraper smoke test
```

There is no test suite, linter, or build step. The `__main__` blocks in each module are the de facto smoke tests; run them against a real URL to verify scraper changes.

Server logs go to stdout and to `pipeline.log` (untracked).

### API key

`analyzers/ai_analyzer.py` reads `os.environ["GROQ_API_KEY"]` **at import time**, so a missing key raises `KeyError` rather than a clean error — and because imports are deferred (below), it surfaces only when the pipeline reaches stage 4, after minutes of scraping. Two stale references exist: `README.md` and the `/api/health` check in `server.py` still say `ANTHROPIC_API_KEY`, which makes the dashboard's health dot report red even when the real key is set.

## Architecture

Three layers, each importable and runnable standalone:

- `scrapers/web_scraper.py` — fetches the main page, extracts meta/nav/prices/socials/contacts/tech fingerprints, follows up to 3 discovered pricing sub-pages. Returns a `ScrapeResult` dataclass. Never raises on network failure: `fetch()` returns `None` and errors accumulate in `result.errors` with `status="error"`.
- `scrapers/social_scraper.py` — takes the `social_links` dict found by the web scraper and scrapes Telegram (`t.me/s/<handle>` preview), Facebook (`mbasic.`), Instagram (JSON embedded in `<script>`). Each returns a `SocialProfile`; failures are recorded in `profile.error`, not raised.
- `analyzers/ai_analyzer.py` — one Groq chat completion (`llama-3.3-70b-versatile`) per analysis. The prompt embeds a literal JSON template the model must fill; `_parse_json` strips markdown fences before `json.loads`. On failure returns `{"error": ...}` instead of raising — callers check for the `"error"` key.

`pipeline.py` orchestrates them through six named stages (`PipelineStatus.STAGES`) and is the only place that decides which stage failures are fatal: a website-scrape exception aborts, social/review/AI failures are logged as warnings and the pipeline continues with partial data.

`server.py` runs each pipeline in a daemon thread, pushing every `on_progress` callback into a per-job `queue.Queue` that `/api/stream/<job_id>` drains as SSE. `active_jobs` is an in-process dict — jobs and their queues do not survive a restart, and there is no cleanup, so state is per-process only.

`templates/index.html` is the whole frontend (no build, no framework, ~860 lines): views for analyze / history / compare, an `EventSource` on the stream endpoint, and rendering of the analysis JSON.

### Contracts to keep in sync

- **Stage names.** `PipelineStatus.STAGES` in `pipeline.py` must match `stageMap` in `index.html` (which also hardcodes five `#st0`–`#st4` elements). Adding a stage means touching both.
- **Analysis JSON shape.** The template inside `ANALYSIS_PROMPT` is the schema. `/api/results` in `server.py` and the dashboard's render functions both read specific keys (`threat_level`, `pricing`, `strengths`, …) off it. Changing a prompt field breaks the summary list and the detail view.
- **Saved-result envelope.** Files in `results/` are the serialized `PipelineStatus`, so the analysis lives at `result.result.analysis` and the compare endpoint reads it from there. `load_saved_results()` adds a `_filename` key that isn't on the dataclass.

### Deferred imports

`pipeline.py` imports the scrapers/analyzer *inside* `run_pipeline()`, and `server.py` imports `pipeline` inside each route. This keeps Flask startup fast and lets the server boot without `GROQ_API_KEY`. Don't hoist these to module level — the health endpoint and dashboard would stop loading without a key.

## Known rough edges

- `SocialProfile` in `social_scraper.py` uses `Optional[int]` before `typing.Optional` is imported, patched afterwards via `__annotations__`. Moving the import to the top and deleting the patch is the fix; don't reproduce the pattern.
- Google review scraping targets Google's obfuscated result-div class names (`BNeawe`, `VwiC3b`, …) and is rate-limited/blocked in practice — an empty `review_snippets` list is the normal case, not a bug.
- Facebook and Instagram return near-empty profiles without authentication; Telegram is the only reliably useful platform.
- `/api/results/<filename>` and `/api/compare` join user-supplied filenames onto `RESULTS_DIR` without validation.
- The dashboard hardcodes `const API = "http://localhost:5000"`, so it only works when served on that port despite `PORT` being configurable.
