# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

The spec is `prd.md` (PRD v2.0, ticket RT-001). The milestone plan (M0–M11) is at `~/.claude/plans/read-prd-md-and-propose-bubbly-chipmunk.md`. All milestones M0–M11 are complete (scaffolding, LaTeX validator, LaTeX parser, JD analyzer, programmatic scorer, Gemini client and prompts, tailor pipeline and `POST /api/tailor`, frontend tailoring screen, analysis panel, archive backend, archive frontend, real-Gemini prompt tuning and acceptance walkthrough), plus fix milestones M11.1 (content preservation) M11.2 (ticket RT-003: meaningful rewriting with two-layer fact validation) and M11.3 (latency: optional verifier call and an overall deadline). The current prompt versions are `tailor-v4` and `verify-v1`. The pipeline lives in `app/tailor/`, not `app/pipeline.py` as the plan originally said. Build one milestone at a time and don't start the next one unless asked.

Note: `prd.md` was extracted from a PDF, so page headers ("Page N of 12") show up in the middle of sections and tables. The programmatic scoring table, for example, is split across FR-04 and FR-05.

## Commands

Backend (`backend/`, Python 3.12 managed by `uv`; run these from `backend/`):
- Install/sync deps: `uv sync`
- Dev server: `uv run uvicorn app.main:app --reload --port 8000` (health check: `curl localhost:8000/api/health`)
- All tests: `uv run pytest`
- Single test: `uv run pytest tests/test_health.py::test_health`
- Lint/format: `uv run ruff check .` and `uv run ruff format .`
- Add a dependency: `uv add <pkg>` (dev-only: `uv add --dev <pkg>`)
- Config comes from `backend/.env` (copy `.env.example`), loaded through `app/config.py` (`get_settings()`). Tailoring latency settings (M11.3):
  - `ENABLE_VERIFY_CALL` (default `false`): run the second, semantic-verifier LLM call. It adds about 12 s, more with retries, and uses extra free-tier quota.
  - `TAILOR_DEADLINE_SECONDS` (default `90`): the overall limit for one `POST /api/tailor`, including retries and backoff.

Frontend (`frontend/`, Next.js 16 + React 19 + TypeScript; run these from `frontend/`):
- Install: `npm install`
- Dev server: `npm run dev` (http://localhost:3000)
- Lint: `npm run lint`. Typecheck: `npx tsc --noEmit`. Production build: `npm run build`
- The backend URL is `API_BASE_URL` in `src/lib/api.ts`, from `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`). It is inlined at build time, so rebuild after changing it. CORS on the backend allows only `localhost:3000`/`127.0.0.1:3000` (`cors_origins` in settings).
- Next.js 16 has breaking changes compared with older versions. Read `frontend/AGENTS.md` and the bundled docs in `frontend/node_modules/next/dist/docs/` before writing frontend code. `LayoutProps`/`PageProps` are generated types that only exist after `next dev`/`next build`, so use explicit prop types to keep `tsc --noEmit` passing.

## Layout

- `backend/app/main.py`: `create_app(tailor_pipeline=None, archive_service=None)` factory. It registers error handlers, `/api/health`, the tailor router (`app/api/routes/tailor.py`) and the archive router (`app/archive/routes.py`). The lifespan runs `archive_service.init()` on startup and `close()` on shutdown. Error schemas shared by every resource live in `app/api/schemas.py` and all handlers in `app/api/errors.py`. The feature packages never import each other.
- `backend/app/latex/`: `validator.py` (`validate_latex`), `parser.py` (`parse_resume`, `reconstruct`) and `models.py`.
  - The parser finds structure on `mask_latex()`, a same-length copy of the source with comments, escapes and verbatim blanked. Offsets therefore always line up with the original.
  - Segments are sliced from the source, so `reconstruct(parse_resume(x)) == x` must always hold.
  - A section that fails to parse becomes `kind="raw"` with an `error`, and must be passed through untouched.
  - `Bullet`/`Entry` keep absolute `start`/`end` offsets for splicing rewrites back in.
  - `units.py` (`extract_units` → `ResumeUnits`) gives every bullet a stable id `<prefix><entry#>.b<bullet#>` (`exp1.b3`; prefixes `exp`, `proj`, `edu`, `sum`, `sec`; entry numbers count across sections of the same type). Bullets outside any entry form their own pseudo-entry. A paragraph-only summary (no bullets, no `%` comments) is one unit `sum1.b1`. Skills sections are not split; they keep ids `s<segment index>` (`section_id`). Entry labels get ", <title>" when two entries share an organization.
- `backend/app/jd/`: `analyzer.py` (`analyze_jd` → `JDRequirements` in `models.py`) and `taxonomy.py` (loads `skills_taxonomy.json`, the single list of skills, aliases and certifications).
  - Weights: high = mandatory tech, medium = preferred tech, low = soft.
  - "Other" JD sections (about us, benefits, pay) are ignored.
  - Ambiguous words (Go, R, REST, Spark) are listed under `case_sensitive` in the JSON.
  - Changing the taxonomy JSON can change the exact skill lists asserted in `tests/jd/test_analyzer.py`.
  - The LaTeX parser also uses the taxonomy (`is_tech_term`) to drop version numbers like "Python 3.11" from metrics.
  - Hierarchy (M11.2, taxonomy version 3): `parents` lists broader skills a skill is evidence for (PostgreSQL/MySQL → Relational Databases + SQL, Redis → Caching, Express/FastAPI/Flask/Django → Backend Frameworks, GitHub Actions → CI/CD, pytest → Unit Testing + Test Automation, …). `Taxonomy.ancestors(name)` walks it transitively and `supporting(name, present)` returns the present children. It only goes upward, so PostgreSQL never supports MongoDB. `implies` stays a full-credit equivalence (Amazon S3 → AWS); a parent is a partial match. "query performance" is an alias of Query Optimization.
  - `concept: true` marks 39 practices (Query Optimization, Code Review, Test Automation, CI/CD, Caching, …) as opposed to named tools or platforms. Layer A treats concepts differently (see `guards.py`). Architecture claims such as Microservices are deliberately not concepts.
- `backend/app/matching/` (M11.2): `matcher.py` (`match_requirements(resume, jd)` → `MatchResult`) and `models.py` (`RequirementMatch`: requirement, kind, status `matched`/`partially_matched`/`missing`, evidence quoted from the resume, reason, credit 0–1, `bullet_ids`, `match_type`). It is shared by the scorer, the tailoring prompt (the matching plan) and the API (`requirement_matches`). It holds the skill matching and TF-IDF code that used to live in the scorer, and uses no new dependencies.
  - Skill tiers and credit: exact / alias / implied → matched, 1.0; fuzzy (version suffix, rapidfuzz typo ratio ≥ 90, or all words of a multi-word skill in one line) → matched, `FUZZY_CREDIT` 0.8; related through the hierarchy (a child skill in the resume) → partially_matched, `HIERARCHY_CREDIT` 0.5; otherwise missing, 0.
  - Responsibilities: TF-IDF cosine against each resume bullet (by unit id). Skill mentions become canonical tokens plus one token per hierarchy parent, so a PostgreSQL bullet also counts as "relational databases". Credit is `min(1, best / ALIGNMENT_FULL_SIMILARITY)` (0.35). Matched at ≥ 0.35, partial at ≥ `ALIGNMENT_PARTIAL_SIMILARITY` (0.15). The evidence is the best bullet, and `bullet_ids` lists up to 3 bullets ≥ 0.15.
- `backend/app/scoring/`: `scorer.py` (`score_resume(ParsedResume, JDRequirements)` → `ATSScore` in `models.py`, which now includes `requirement_matches`).
  - Weights (35/25/15/15/10). Mandatory and preferred coverage is the sum of match credits divided by the count. Keyword coverage is credit-weighted by JD mentions. Experience alignment is the mean responsibility credit from the matcher. `KeywordMatch` lists matched and partial skills with `match_type` and `credit`; `missing_keywords` lists only the missing ones.
  - A criterion with nothing to measure (for example, no preferred skills in the JD) is `applicable=False`, and the total is rescaled over the rest.
  - Structure checks don't penalise `tabular` inside preamble macros (Jake's Resume); only body tables count.
  - Changing these constants or the taxonomy changes the asserted scores in `tests/scoring/`.
- `backend/app/llm/`: `client.py` (`GeminiClient`), `prompts.py`, `models.py` and `errors.py`.
  - `tailor_resume(resume, jd, keywords?, missing_keywords?, plan?)` returns a `TailoringResponse` with `bullets[{id, text, changed, reason, position?}]` (`text` is the bullet's LaTeX content only; `position` is a 1-based slot within its own entry), `skills_sections[{section_id, latex}]`, `ats_analysis` (LLM score 0–100, reasoning, keywords, suggestions) and `notes`. `verify_rewrites(resume_text, items)` is the second call and returns `VerificationResponse{results[{id, verdict, evidence, problem, wording_only}]}`. `generate_structured(system, user, schema)` is the generic JSON call. `request_count` counts every HTTP request, including retries and repairs. `PROVIDER = "Google Gemini"`.
  - The client owns all retries: 429/5xx back off exponentially (max 3 retries, honouring RetryInfo). SDK retries are disabled via `HttpRetryOptions(attempts=1)`.
  - Each call has a 60 s timeout (`asyncio.wait_for`, plus the SDK timeout in milliseconds). Malformed or schema-invalid JSON gets exactly one repair request.
  - Errors are typed: `GeminiTimeoutError`, `GeminiRateLimitError`, `GeminiResponseError`, `GeminiConfigError`. Each carries `.retryable` for the API layer.
  - Prompts are `string.Template` (`$var`) because LaTeX and JSON braces break `str.format`. Bump `PROMPT_VERSION` whenever a template changes; M6 uses it in the cache key.
  - Since `tailor-v3` only bullets (grouped by entry, with the plain heading as read-only context) and whole skills sections are sent. Headings, dates, macros, the preamble, the raw header and failed sections are never editable, so they cannot change.
  - `tailor-v4` (M11.2) states the goal first (improve the relevant bullets: a strong verb, implementation before outcome, JD wording where the facts support it, every metric kept, "do not play it safe"), then five short hard limits. It sends the matching plan (partial matches first, each with `target_bullets`) and the JD responsibilities. `verify-v1` (`VERIFY_PROMPT_VERSION`, `build_verify_prompt`) gets the plain body text (`resume_body_text`: sections only, no contact header) plus `{id, original, rewrite}` for each bullet that passed Layer A, and must quote exact evidence. The cache key includes both versions.
  - Never log or put prompt/resume/JD text in exception messages. Validation errors are summarised as field path plus message only.
  - Tests mock `sdk_client.aio.models.generate_content` with `AsyncMock` and inject `sleep`. Never make real API calls in tests.
- `backend/app/tailor/`: `pipeline.py` (`TailorPipeline.run(latex, jd)` → `TailorResult` in `models.py`). `POST /api/tailor` is in `app/api/routes/tailor.py`.
  - Steps: parse → analyze JD → score the original (before/after, and the matching plan) → tailoring call → `judge()` (Layer A) → `verify_rewrites()` (Layer B, second call: only when `ENABLE_VERIFY_CALL` is true and something was reworded; otherwise `skip_verification()`) → `finalize()` → validate → page-length warning → score the FINAL resume (after every revert and restore; `requirement_matches` and the suggestions come from it too) → `TailorResult`. Expect 1 LLM request by default (2 with the verifier on and something reworded), plus any retries or repairs (`llm_requests`). Timing stages include `verify` (≈0 ms when the verifier is off). Real runs took about 47 s with one call and about 61 s with the verifier; one retry pushed a run past 100 s, which is why M11.3 added the deadline.
  - `apply.py` (`apply_tailoring`) splices each bullet rewrite into the original bullet's exact span, so the preamble, headings and section order never change. Reordering moves contents between the slots of one entry only. Ordering (`_order`): restored bullets keep their original slot, requested positions are honoured if the slot is free, the rest fill the gaps in original order.
  - `apply.py` is split into `judge(resume, response)` → `Judgement` (Layer A outcomes, `pending_verification()`, `revert()`), then `finalize(judgement)` → `AppliedTailoring` (latex, warnings, changes, `bullet_changes`, check). `apply_tailoring()` = judge + finalize with no verification.
  - Enforcement, never trusting the model: missing ids → original restored + warning "The model dropped N bullets; originals restored"; unknown/duplicate ids → discarded + warning; a rewrite failing a guard → reverted + warning naming the bullet. The final check re-parses every changed section standalone: bullets per entry must be equal and skills not fewer, else that whole section is reverted.
  - Layer A, `guards.py` (M11.2), checked against the WHOLE original resume (`ResumeContext`: body text, numbers, tech, tech + hierarchy ancestors, words). Every problem names its rule:
    - `LaTeX`: structural commands, new unescaped `% & # _`, or new brace errors.
    - `new number`: a number that appears nowhere in the resume. Commas and number words are normalised, so "three" = 3.
    - `lost number`: a number of the original bullet is missing from the rewrite.
    - `unsupported tool`: a non-concept taxonomy tool or platform not supported by the resume, directly or through the hierarchy.
    - `vaguer scope`: a tool or proper-noun/`p95`-style name of the original bullet was dropped. Generic acronyms (API, UI, …) and names that survive inside a kept tool ("REST" → "RESTful") are exempt.
    - `inflated claim`: only the pairs prevent/reduce/cut → eliminate, some/several/many → all/every/entire, and contributed/assisted/helped/supported/participated → led/owned/drove/spearheaded. Generic verb changes ("developed" → "built", "used" → "built") are left to the verifier.
    - `merge`: `merged_into` flags a rewrite that absorbs ≥ 60% of another bullet's distinctive words.
    - Concepts the resume never names ("query optimization" for "tuned slow queries") pass Layer A as `unverified_concepts`: only the verifier can accept them, and they are reverted when verification is unavailable.
    - `skills_problem`: every original skill item kept, item count not lower, and added items only if the resume mentions them or supports them through the hierarchy (so "Relational Databases" is allowed, MongoDB is not).
  - `ENABLE_VERIFY_CALL` off (the default): `skip_verification` sets `content_check.verification = "disabled"`. Layer A results stand, except rewrites naming a practice the resume never names (`unverified_concepts`), which are reverted with "… (ENABLE_VERIFY_CALL is off)". The flag is part of the cache key (`<PROMPT_VERSION>+<VERIFY_PROMPT_VERSION or no-verify>`). `TailorPipeline(enable_verify=…)` overrides the setting; verifier tests use `run_verified()` in `tests/tailor/test_preservation.py`.
  - Layer B, `verify.py`: `verify_rewrites(judgement, client)`. The evidence must occur in `resume_body_text` (case-, whitespace-, quote- and dash-insensitive, LaTeX escapes tolerated, ≥ `MIN_EVIDENCE_CHARS`), otherwise the rewrite is treated as unsupported. Outcomes:
    - supported → keep
    - partially_supported → keep only if `wording_only`
    - unsupported, or no verdict → revert ("semantic check: …")
    - the call raises a `GeminiError` → Layer-A rewrites are kept with the warning "Semantic verification unavailable …" (except unverified concepts), and `content_check.verification = "unavailable"`.
  - `changes_summary` is built by the backend from the per-bullet outcome ("Reworded exp1.b2 (Org): <model's reason>", "Reordered bullets in Org (now b3, b1, b2)", "Reverted … to the original (<rule>: …)", "Restored …: the model omitted it", "Left N bullets unchanged", skills section result). `bullet_changes[{id, label, status, reason, model_reason, before, after, proposed}]` is the structured version. The model's `notes` become `model_notes`, minus any claim about preserving facts (`filter_model_notes`). `content_check` (`ContentCheck`) has bullets in/out per entry and counts of reworded, unchanged, reverted, restored and discarded bullets, plus skill counts.
  - `suggestions.py`: `build_suggestions(final_score)` → `SuggestionGroups{surface (partial matches: "if that work involved X, name it"), missing ("the app will never add it; add it only if you have real experience"), other (structure issues)}`. It never suggests fabricating. `improvement_summary` is the flattened version; the model's free-text suggestions are no longer shown.
  - `TailorResult` also carries `requirement_matches` (final resume), `suggestions`, `bullet_changes`, `provider`, `model` (the exact `GEMINI_MODEL` used) and `llm_requests`. `content_check` adds `verification` and `semantic_reverts`.
  - A false positive just keeps the original bullet, but M11.1's strictness was too much (0 reworded in a real run). Prefer handing semantics to the verifier over adding Layer A word lists. Changing their word lists can change assertions in `tests/tailor/test_preservation.py` and `test_guards.py` (fixture: `tests/fixtures/resumes/preservation_resume.tex`).
  - Cache: an in-memory LRU (`ResultCache`, 64 entries, per process) keyed by `sha256(prompt versions + verify flag, model, latex, jd)`. Only successes are cached; hits return `cached: true`.
  - The Gemini client is created lazily on the first request, so the app starts without a key. A missing key gives 500 `gemini_not_configured`.
  - Errors (`app/api/errors.py`) look like `{"error": {code, message, retryable, details, retry_after_seconds}}`:
    - 422 `invalid_request`: empty, blank, missing or oversized fields (validated without stripping)
    - 400 `invalid_input`: no `\begin{document}` or no sections
    - 502 `gemini_timeout`, `gemini_rate_limited` or `gemini_bad_response`
    - 500 `gemini_not_configured` or `internal_error`
    - 504 `gemini_deadline` (retryable): the route wraps `pipeline.run` in `asyncio.wait_for(…, TAILOR_DEADLINE_SECONDS)`. On expiry the run is cancelled (nothing is cached) and the message is "Tailoring took longer than 90 seconds and was stopped. Try again." `GeminiError` does not subclass `TimeoutError`, so a model timeout stays a 502 `gemini_timeout`. `create_app(tailor_deadline_seconds=…)` overrides the limit (tests use a `SlowPipeline` fake).
  - Unexpected exceptions are caught in the route and only their type and stack frames are logged, never the message. Responses never echo the submitted text.
  - The `app.*` INFO logs (sizes, timings, cache hits) are not shown under plain uvicorn because no logging config is set up yet.
  - Tests use `FakeGemini` in `tests/tailor/conftest.py` via `create_app(tailor_pipeline=TailorPipeline(client_factory=lambda: fake))`.
- `backend/app/archive/`: `models.py` (SQLAlchemy `ArchivedResume` table plus the pydantic `ArchivedResumeOut`), `service.py` (`ArchiveService`) and `routes.py` (`/api/archive`).
  - Endpoints: `POST` (multipart `file`, `company_name`, `job_role?`) → 201; `GET` lists metadata, newest first (ties broken by id); `GET /{id}/download` returns the PDF with the original name in `Content-Disposition`, RFC 5987 encoded; `DELETE /{id}` → 204.
  - It is fully independent of tailoring: its own `Base`, engine and directory. `tests/archive` asserts it imports nothing from `app.tailor`, `app.llm`, `app.jd`, `app.latex` or `app.scoring`.
  - Database: async SQLAlchemy 2.1 with `aiosqlite` and `NullPool`, because pooled aiosqlite connections must not cross event loops. Tables are created by `init()`, which is idempotent and also runs lazily from the route dependency. SQLite drops tzinfo, so `ArchivedResumeOut` re-attaches UTC and timestamps serialise as `...Z`.
  - Storage: `settings.archive_upload_dir` (default `backend/uploads/resumes/`) and `settings.archive_database_url` (default `backend/data/app.db`). Both resolve against `BACKEND_DIR`, not the working directory, can be overridden with the `ARCHIVE_UPLOAD_DIR` and `ARCHIVE_DATABASE_URL` env vars, and are git-ignored.
  - Uploads are streamed to `<uuid>.pdf.part`, checked, then renamed to `<uuid>.pdf`. The client's file name is sanitised and stored as metadata only, never used as a path. `file_path` is relative to the upload dir, and `path_for()` refuses anything that resolves outside it.
  - Validation (all 422 `invalid_upload`, with `details[].field`): blank company (validated by the service, so missing, empty and blank give the same message); company or role over 200 chars; extension not `.pdf`; MIME not `application/pdf` or `application/x-pdf`; no `%PDF-` signature; an empty file; over 10 MB (`MAX_UPLOAD_BYTES`, checked while streaming). Rejected uploads leave no files behind.
  - Missing id, or a record whose file is gone, gives 404 `not_found`. Delete removes the row first, then the file, and still returns 204 if the file was already missing.
  - CORS exposes `Content-Disposition` so the M10 frontend can read download file names.
  - Tests use a per-test `tmp_path` dir and DB, an injectable `clock` for ordering, and `with TestClient(...)` so the lifespan runs.
- Manual real-API test: `cd backend && GEMINI_MODEL=gemini-3.5-flash uv run python -I scripts/test_real_gemini.py`.
  - It runs the unmocked pipeline on `scripts/fixtures/` (a fictional Jake's-style resume and a mid-level Python backend JD) and prints stage timings (from `TailorResult.timings_ms`, both LLM calls), before/after and LLM scores, LLM usage (provider, model, HTTP requests, verification status), the content check, each revert and restore with its reason and the rejected rewrite, before/after for reworded bullets, the requirement-matches table, both suggestion groups, the backend `changes_summary`, the model's notes, validation, and the tailored bullets and skills lines.
  - It writes the full output to `scripts/output/tailored_resume.tex` (git-ignored) and exits 1 with a hint on any Gemini error.
  - It is not part of pytest. All pytest tests are network-free; this was verified with non-localhost sockets blocked.
- Prompt `tailor-v2` (M11) adds truthfulness rules after `tailor-v1` fabricated claims in the real run: the fixture's "Redis-backed job queue" became Celery, a Flask project gained FastAPI, a new bullet was invented, and internal labels like "Test Automation" were bolded in.
  - The pipeline now passes the original resume's missing JD skills (from `original_score.missing_keywords`) as `not_in_resume`. Gemini must not introduce named technologies from that list, and may only relabel practices the resume already describes, in the same role.
  - The job payload carries each skill's `jd_wording` (from `matched_terms`) so the model uses the JD's phrases, not taxonomy labels.
  - Rules also cover: no new bullets, keep facts and scope, keep the original's bold style, keep `%` comments, a complete `changes_summary`, and an honest self-score.
  - Expect lower but truthful scores for candidates who lack required skills; the >90 target is only reachable when the resume really has them.
- Prompt `tailor-v3` (M11.1) came after a manual review of real output found `tailor-v2` deleting whole bullets and project details, strengthening claims ("prevent" → "eliminate"), replacing named platforms with invented scope, and claiming "all factual details preserved". v3 switches to bullet ids and drops the one-page rule. Real runs on the script fixture (2026-11-13): 8/8 bullets kept, 0 reverts. Gemini was very conservative (0–3 bullets reworded, mostly added `\textbf`), so the programmatic score stayed at 50.8 before and after.
- Prompts `tailor-v4` + `verify-v1` (M11.2). Real run on the script fixture (2026-11-13, `gemini-3.5-flash`):
  - Requests and time: 2 LLM requests, about 61 s in total (tailor ~49 s, verify ~12 s).
  - Bullets: 5 reworded, 0 unchanged, 3 reverted, 0 restored, and 8/8 in every entry.
  - Reverts: two were correct semantic catches ("deployed production infrastructure", "automated monthly reports"). One was a Layer A false positive ("query optimization" for "tuned slow queries"), which led to the taxonomy `concept` flag.
  - Scores: LLM 82; programmatic 60.7 → 63.1. The new hierarchy credit also raised the original's score from 50.8.
  - The free tier ran out of quota right after (Gemini asked for a wait of about 80 minutes), so the run after the concept fix is still pending.
- `backend/tests/`: pytest tests (`pythonpath = ["."]`, so tests import `app.*`). Fixtures live in `tests/fixtures/resumes/`, `tests/fixtures/jds/` and `tests/fixtures/scoring/` (a JD-tailored resume and an unrelated one).
- `frontend/src/app/`: `/` renders `TailorScreen` and `/archive` renders `ArchiveScreen`. `NavBar` (in the root layout) links both pages. `layout.tsx` loads the fonts (Inter → `--font-sans`, JetBrains Mono → `--font-mono`, self-hosted by `next/font`, so builds need network) and renders `Starfield`, `NavBar` and `DisclosureBanner`.
- `frontend/src/components/tailor/`: `TailorScreen` (state machine: idle → loading → success | error, plus Cancel), `LatexCode` (`LatexEditor` and `LatexView`), `LatexOutput` (copy/download), `ValidationStatus` and `ErrorCard` (titles per backend error code). Styles are in `tailor.module.css`. `ValidationStatus` shows M1 issues only; pipeline warnings live in the analysis panel.
- `frontend/src/components/analysis/`: `AnalysisPanel` mounts only for a successful result, below the output, and is keyed by `runId` so its fade/slide-in replays every run. Its sections, in order:
  - `ScoreOverview`: "LLM assessment" ("Model-generated analysis", with "Provider: … · Model: …" from `result.provider`/`result.model`) and the programmatic score, plus "Before → After" from `original_programmatic_score`
  - collapsible LLM reasoning, labelled as model-generated
  - `RequirementMatches`: every requirement with a matched/partial/missing status chip; expanding it shows the reason, the evidence quote and the bullet ids
  - `KeywordGroups`: four chip groups. Counts are matched/total, where total = matched + missing of that priority. Hovering a chip shows the alias, fuzzy or hierarchy evidence, and fuzzy and related (partial) chips are marked `~`.
  - `CriteriaBreakdown`: the 5 M4 criteria; non-applicable ones show "n/a"
  - two suggestion groups from `result.suggestions`: "Supported by your experience, consider surfacing" and "Missing from your resume" (the app never adds these), then structure issues
  - a warnings card combining `result.warnings` and `programmatic_score.warnings`

  Score bands live in `score.ts` (`scoreTone`: ≥90 green `#00ff88`, 60–89 yellow `#f0c040`, <60 red `#ff4444`), and the colours are CSS variables on `.panel` in `analysis.module.css`. Between the suggestions and the warnings sit a collapsible "What changed" section (`bullet_changes`: each reworded bullet with the model's reason and a before/after, each revert or restore with the backend's reason and the rejected rewrite; then reorder and skills lines from `changes_summary`, headed by the `content_check` counts and verification status) and a collapsible "Model's notes" section (`result.model_notes`, labelled as the model's own words).
- `frontend/src/components/archive/`: the archive page. It is independent of tailoring and has no localStorage; all data comes from the API.
  - `ArchiveScreen` owns the list state (loading/ready/error), the delete confirmation flow and the bottom-right toast.
  - `UploadForm` is a drop zone that is also the native file input (`<label>` wrapping an `sr-only` input). It pre-checks `.pdf`, MIME, empty and over-10 MB on the client; the backend re-checks everything. It shows distinct messages for 422, 5xx and backend unreachable, and a spinner while uploading.
  - `ArchiveTable` shows the API's newest-first order. The table scrolls horizontally on narrow screens.
  - `ConfirmDialog` is a native `<dialog>` with `showModal()`, giving a focus trap, Esc and backdrop-click to cancel. It is not `confirm()`.
  - Styles are in `archive.module.css`.
  - The API client is `src/lib/archiveApi.ts`: `listArchive`, `uploadResume` (FormData; don't set Content-Type yourself), `deleteResume`, `downloadResume` (fetch → blob → save as `original_filename`), `checkPdf`, `formatBytes` and `formatUploaded` (locale date via `Intl`). It reuses `API_BASE_URL`, `ApiError` and `toApiError` from `api.ts`.
  - Data loading follows react-hooks 7: the `set-state-in-effect` rule flags any function containing `setState` that is called from an effect, even after `await`. So pure `fetchList()` returns the next state, the effect applies it in a `.then` callback, and handlers do `setList(await fetchList())`.
- `frontend/src/lib/`: `api.ts` (`tailorResume()`, TS types mirroring the backend's `TailorResult`, `ApiError` with `code`/`retryable`, and a 100 s client timeout (`REQUEST_TIMEOUT_MS`, just above the backend's 90 s deadline, so the structured `gemini_deadline` error normally arrives first; `client_timeout` only fires if the backend hangs). Every error, including `gemini_deadline` and `client_timeout`, shows `ErrorCard` with Retry, and the inputs survive in localStorage), `latexHighlight.ts` (dependency-free tokenizer) and `useLocalStorage.ts`.
- Theme: CSS variables at the top of `globals.css` (`--accent #00ff88`, `--accent-dim`, `--panel`, `--glow` …). It also defines global `.card`, `.btn .btn-primary/.btn-secondary/.btn-small`, `.input`, `.chip` (for M8) and `.muted`. Animations respect `prefers-reduced-motion`.
- The LaTeX input is a transparent `<textarea>` over a highlighted `<pre>`. Both share every text metric in `tailor.module.css` (font, size, line-height, padding, `white-space: pre`, no ligatures). `tokenizeLatex` must never change the text: joining its tokens must equal the input, or the caret drifts.
- localStorage goes through `useLocalStorage` (built on `useSyncExternalStore` with a server snapshot), never `useEffect` + `setState`. `eslint-plugin-react-hooks` 7 rejects setState-in-effect and reading `Date.now()` during render. Keys are prefixed `resume-tailr.` (inputs and disclosure dismissal).

## Core constraint: content preservation (M11.1, M11.2)

**The tailor may REWORD, REORDER and EMPHASIZE, but must NEVER DELETE content or ADD facts. The user decides what to cut, not the app.**
- This overrides the PRD's "target 1 page" goal. Page length is not an objective: when the output is probably longer than one page, the pipeline adds a warning (`page_length_warning`, a rough line estimate against `PAGE_LINES`) and trims nothing.
- It is enforced in the backend (`app/tailor/apply.py` + `guards.py`), never trusted to the prompt. Any new tailoring feature must keep these guarantees: every original bullet present, bullets per entry equal, skills after ≥ before, no numbers, tools or names the original doesn't support, no strengthened claims.
- Truthful output means scores stay low when the candidate lacks JD skills. Don't loosen the guards to raise scores.
- Validation has two layers: deterministic Layer A (whole-resume facts, tools, numbers, certainty) and a semantic verifier (Layer B, a second LLM call, off by default since M11.3: `ENABLE_VERIFY_CALL`). Neither trusts the model: verifier verdicts need evidence that really occurs in the resume. Within these limits the tailor should actively improve relevant bullets; leaving everything unchanged is a failure too.
- UI labels are provider-neutral ("LLM assessment", "Model-generated analysis", "Provider: … / Model: …"). The only place "Google Gemini" is hardcoded is the required disclosure banner.

## What it is

A local, single-user web app with two **independent** features. Do not couple them.
1. **Resume tailoring**: takes LaTeX resume source and a plain-text JD, then returns tailored LaTeX, a syntax-validation report, and two ATS alignment scores. Generation is one-shot: no iterative refinement, no PDF compilation.
2. **PDF archive**: upload, list, download and delete PDFs tagged with a company name (required) and a role (optional).

No auth, no accounts, no cloud storage.

## Planned stack

- Frontend: Next.js + React + TypeScript. Screen 1 is tailoring (input left, output right, analysis below). Screen 2 is the archive.
- Backend: Python + FastAPI with async endpoints. All Gemini calls go through the backend.
- LLM: Google Gemini free tier, using the `google-genai` SDK. The model is set by `GEMINI_MODEL` (default `gemini-3.5-flash`, in `app/config.py`) and must not be hardcoded elsewhere.
  - Verified in M11 (2026-11-13): `gemini-2.0-flash`, `gemini-1.5-flash` and `gemini-2.5-flash` return 404 for new keys ("no longer available to new users"). `gemini-3.7-flash` and `gemini-3.8-flash` were listed but kept returning 503/504 ("high demand").
  - `gemini-3.5-flash` worked (about 20 s per tailoring call). It is now the code default; `backend/.env` (not `.env.example`) is the file the app reads.
  - `client.models.list()` lists models that still 404 for new users. Probe with a real `generate_content` call before trusting the list.
  - Even working models hit 503 spikes that outlast the client's ~7 s of backoff. Rerun, or use the UI's Retry. The key goes in `GEMINI_API_KEY` via `backend/.env` and must never reach the frontend.
- The backend owns the preamble: Gemini only sees and rewrites body sections, and the original preamble is reattached verbatim so the response is still a complete `.tex` file.
- Archive: SQLite + SQLAlchemy for metadata, with files under `./uploads/resumes/`. Data must persist across restarts.

## Tailoring pipeline (backend)

These modules must stay separate and swappable behind clear interfaces (PRD maintainability requirement):

1. **LaTeX parser** (rule-based, regex): splits the source into preamble/macros/custom commands and editable sections (experience, projects, education, skills, summary). It also pulls out bullets, dates, companies, titles, skills and certifications, and keeps the full original source for reconstruction. It must handle `\section`, `\subsection`, `\resumeSubheading` and custom `itemize` blocks. **A section that fails to parse passes through unmodified.** Target time is under 2s.
2. **JD analyzer** (rule-based keywords + Gemini): sorts requirements into mandatory skills (high weight), preferred skills (medium), soft skills (low), responsibilities, experience level, domain terms and certifications.
3. **Gemini client**: receives bullets with stable ids, skills sections, weighted requirements, a keyword priority list, the requirement-matching plan and the content-preservation rules (see the core constraint; no one-page constraint since M11.1). It returns per-bullet rewrites with reasons plus JSON ATS analysis (LLM score and reasoning); the backend checks them in two layers and splices them into the original LaTeX. A second call verifies the rewrites. It handles 4xx/5xx errors, retries 429s with backoff, uses a 60s timeout, and survives malformed responses. Don't re-call it when inputs haven't changed. **Prompts live in a separate config/constants module.**
4. **Programmatic scorer** (deterministic, total 100):
   - Mandatory skills coverage, exact + fuzzy matching: 35
   - Experience/responsibility alignment, TF-IDF/cosine similarity of JD responsibilities vs resume bullets: 25
   - Preferred skills coverage, exact + fuzzy matching: 15
   - Technical keyword coverage, % of JD technical terms present: 15
   - ATS structure/readability, rule checks (standard headings, bullets, no complex formatting): 10
5. **LaTeX validator** (regex): checks for unmatched `{}`, `[]` and `()`, unmatched `\begin`/`\end`, and missing `\documentclass`, `\begin{document}` or `\end{document}`. If it finds errors, the output is still returned and the UI shows a warning banner.

Both scores target above 90. The UI must also show matched/missing mandatory and preferred keywords, a skill alignment breakdown, and an improvement summary.

## Constraints that are easy to miss

- Never log full resume or JD text.
- Show a one-time disclosure: "Your resume content is sent to the Google Gemini API for processing."
- User inputs must survive API errors in the UI, and the retry option shown on error should reuse them.
- Archive uploads: `.pdf` extension only, a valid PDF MIME type, and a 10 MB max. List newest first and confirm before deleting.
- Archive metadata: id, company_name, job_role, file path, original filename, size in bytes, upload timestamp (UTC).
