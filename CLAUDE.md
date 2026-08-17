# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Competitor Intelligence Pipeline — scrapes a competitor's website, pricing pages, social profiles and review snippets, then sends everything to an LLM for a structured analysis. Flask backend + single-file dashboard, results persisted as JSON files.

The domain is Ukrainian ISP/telecom competitors: scraper regexes, prompts, LLM output and all UI strings are Ukrainian (prices in UAH, `грн`/`₴` patterns). Keep new user-facing strings and prompt text in Ukrainian to match.

## Commands

```bash
docker compose up -d                 # app + Ollama + model pull; dashboard on :5000
docker compose logs -f ollama-init   # first run downloads ~3GB, app waits for it
docker compose logs -f app

pip install -r requirements.txt      # or run locally against a host Ollama
ollama pull gemma3:4b                # analysis runs on a local model, no API keys
python server.py                     # dashboard + API on http://localhost:5000 (PORT env overrides)

python pipeline.py https://lanet.ua                  # full pipeline, CLI, prints progress + saves JSON
python scrapers/web_scraper.py https://ukrtelecom.ua # scraper only, dumps ScrapeResult JSON
python analyzers/ai_analyzer.py                      # LLM health check + demo analysis
python -m scrapers.social_scraper                    # telegram scraper smoke test

pytest                               # ~30 tests, no Ollama needed
pytest tests/test_ai_analyzer.py::test_failed_step_does_not_lose_the_rest -v
```

No linter or build step. Server logs go to stdout and to `pipeline.log` (untracked).

### LLM configuration

Everything is env-driven, defaults in `analyzers/llm_client.py`: `OLLAMA_BASE_URL`, `LLM_MODEL` (default `gemma3:4b`), `LLM_NUM_CTX`, `LLM_TIMEOUT`, `LLM_TEMPERATURE`, `LLM_RETRIES`. The client is a lazy singleton (`get_client()`), so importing any module without a running Ollama is safe — failures surface as `{"error": ...}` from `analyze_competitor`, not as import errors.

### Docker layout

`docker-compose.yml` runs three services: `ollama` (models in the `ollama-models` volume), a one-shot `ollama-init` that `ollama pull`s `$LLM_MODEL` and exits, and `app`, which waits on `service_completed_successfully` so it never starts before the model exists. Reports bind-mount to `./results` on the host. Tunables come from `.env` (`APP_PORT`, `LLM_MODEL`, `LLM_TIMEOUT`, …); `.env.example` documents them.

The image runs gunicorn with **`--workers 1 --threads 16 --timeout 0`**, and those values are load-bearing: `active_jobs` and the SSE queues live in process memory, so a second worker would route `/api/stream/<job_id>` to a process that has never heard of that job; `--timeout 0` stops gunicorn from killing a worker that is holding an SSE connection open for the minutes an analysis takes.

### Testing without a model

`tests/conftest.py` provides a `FakeSession` transport injected via `OllamaClient(session=...)`, plus a `make_client` fixture that queues canned model replies. Both `analyze_competitor` and `generate_comparison_matrix` take a `client=` argument for exactly this. Add tests here rather than mocking `requests` globally.

## Architecture

Three layers, each importable and runnable standalone:

- `scrapers/web_scraper.py` — fetches the main page, extracts meta/nav/prices/socials/contacts/tech fingerprints, follows up to 3 discovered pricing sub-pages. Returns a `ScrapeResult` dataclass. Never raises on network failure: `fetch()` returns `None` and errors accumulate in `result.errors` with `status="error"`.
- `scrapers/social_scraper.py` — takes the `social_links` dict found by the web scraper and scrapes Telegram (`t.me/s/<handle>` preview), Facebook (`mbasic.`), Instagram (JSON embedded in `<script>`). Each returns a `SocialProfile`; failures are recorded in `profile.error`, not raised.
- `analyzers/ai_analyzer.py` — five narrow LLM calls instead of one big one (profile → pricing → social → reputation → synthesis), each with its own JSON schema from `analyzers/schemas.py` passed as Ollama's `format`. A step with no input data is skipped without calling the model; a step that fails falls back to an `_empty_*()` block and is recorded in `_llm.failed_steps`, so one failure never loses the whole analysis. Returns `{"error": ...}` only when Ollama itself is unreachable or the model isn't pulled.

`pipeline.py` orchestrates them through six named stages (`PipelineStatus.STAGES`) and is the only place that decides which stage failures are fatal: a website-scrape exception aborts, social/review/AI failures are logged as warnings and the pipeline continues with partial data.

`server.py` runs each pipeline in a daemon thread, pushing every `on_progress` callback into a per-job `queue.Queue` that `/api/stream/<job_id>` drains as SSE. `active_jobs` is an in-process dict — jobs and their queues do not survive a restart, and there is no cleanup, so state is per-process only.

`templates/index.html` is the whole frontend (no build, no framework, ~860 lines): views for analyze / history / compare, an `EventSource` on the stream endpoint, and rendering of the analysis JSON.

### Contracts to keep in sync

- **Stage names.** `PipelineStatus.STAGES` in `pipeline.py` must match `stageMap` in `index.html` (which also hardcodes five `#st0`–`#st4` elements). Adding a stage means touching both.
- **Analysis JSON shape.** `analyze_competitor` assembles the five step outputs into one dict that `/api/results` and the dashboard's render functions read by key (`threat_level`, `pricing`, `strengths`, …). The enum *values* are part of the contract too: `index.html` maps them to colors (`threatColor`) and CSS classes (`priority-висока`), so the Ukrainian strings in `analyzers/schemas.py` cannot change without touching the template.
- **Saved-result envelope.** Files in `results/` are the serialized `PipelineStatus`, so the analysis lives at `result.result.analysis` and the compare endpoint reads it from there. `load_saved_results()` adds a `_filename` key that isn't on the dataclass.

### Numbers are computed in Python, never by the model

`price_stats()`, `total_followers()`, `most_active_platform()` and `score_analysis()` derive every number from scraped data. The prompts explicitly tell the model *not* to compute averages or sums, because 4B models get arithmetic wrong. Keep it that way when adding fields: ask the model for judgement, compute the figures yourself. A side effect worth preserving — the comparison matrix is deterministic, so re-running it on the same reports gives the same table.

### Deferred imports

`pipeline.py` imports the scrapers/analyzer *inside* `run_pipeline()`, and `server.py` imports `pipeline` inside each route. This keeps Flask startup fast. Import-time safety no longer depends on it (the LLM client is lazy), but hoisting them still isn't worth it.

## Known rough edges

- Google review scraping targets Google's obfuscated result-div class names (`BNeawe`, `VwiC3b`, …) and is rate-limited/blocked in practice — an empty `review_snippets` list is the normal case, not a bug.
- Facebook and Instagram return near-empty profiles without authentication; Telegram is the only reliably useful platform.
- `/api/results/<filename>` and `/api/compare` join user-supplied filenames onto `RESULTS_DIR` without validation.
- Scrapers pass `resp.content` (not `resp.text`) to BeautifulSoup so charset comes from the HTML meta tag. Sites that omit charset from the `Content-Type` header decode as ISO-8859-1 otherwise, which silently mangles Cyrillic and makes the `грн` price patterns miss everything.
