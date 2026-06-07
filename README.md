# PR Review Agent

AI-powered pull request code review system using Claude API with multi-turn reasoning.
Designed for teams handling 50+ PRs/week with ~2M tokens weekly budget.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                         PR Review Agent                               │
│                                                                       │
│  GitHub PR ──► Agent ──► Turn 1: Bug & Security Scan                 │
│                     │──► Turn 2: Performance & Quality                │
│                     │──► Turn 3: Structured JSON Report               │
│                     └──► Post Comment to GitHub PR                    │
│                                                                       │
│  FastAPI Server ──► REST API ──► Dashboard UI                        │
│  GitHub Webhook ──► Auto-trigger on PR open/update/label             │
│  Task Queue (arq) ──► Async Processing ──► Worker Pool               │
│  Job Store ──► In-Memory / File (JSONL) / Redis                      │
│  Monitoring ──► Prometheus Metrics ──► Grafana                       │
└──────────────────────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Clone & Install

```bash
git clone <this-repo>
cd pr-review-agent
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp config/.env.example config/.env
# Edit config/.env with your API keys
```

Required:
- `ANTHROPIC_API_KEY` (or any provider key — see [Multi-Provider LLM Support](#multi-provider-llm-support)) — for Anthropic
- `GITHUB_TOKEN` — GitHub Personal Access Token with `repo` scope

### 3a. CLI Usage (Quick)

```bash
cd backend
# Load env vars
export $(cat ../config/.env | xargs)  # Linux/Mac
# Or on Windows: use set or a .env loader

# Review a PR (posts comment to GitHub)
python cli.py --repo owner/repo-name --pr 42

# Dry run (no GitHub comment)
python cli.py --repo owner/repo-name --pr 42 --no-comment

# Save full JSON result
python cli.py --repo owner/repo-name --pr 42 --output result.json
```

### 3b. Web Server + Dashboard

```bash
cd backend
export $(cat ../config/.env | xargs)
python server.py

# Open http://localhost:8000
```

### 3c. Worker (Production)

For production deployments with async task queue:

```bash
# Start the arq worker
python scripts/start_worker.py

# Worker reads REDIS_URL and WORKER_CONCURRENCY from environment
```

### 3c. GitHub Actions (Automated)

Copy `.github/workflows/pr-review.yml` to your repository. Add these secrets:
- `ANTHROPIC_API_KEY`
- `GITHUB_TOKEN`

Every PR open/sync/reopen triggers an automatic review.

## Multi-Provider LLM Support

The agent is not locked to Anthropic — the LLM provider is configurable end-to-end. The backend speaks two wire formats:

| Backend format | Used by |
|----------------|---------|
| `anthropic` | Native Anthropic Messages API. The **Base URL** is optional — set it to point at any Anthropic-protocol proxy. The **Model** field accepts any id (e.g. `claude-3-5-sonnet-20241022`, `claude-3-opus-20240229`, `claude-3-haiku-20240307`, or a custom id your proxy exposes). |
| `openai-compatible` | OpenAI Chat Completions and any drop-in compatible endpoint (OpenAI, DeepSeek, Moonshot, Zhipu GLM, Ollama, vLLM, Together, Groq, OpenRouter, …). The **Model** field accepts any id (e.g. `gpt-4o`, `gpt-4o-mini`, `deepseek-chat`, `llama3.1`, fine-tunes, etc.). |

In short: **both formats support custom model names**, and the Anthropic format additionally supports pointing at a custom endpoint (LiteLLM, AWS Bedrock, gateway, etc.).

### Switching providers from the dashboard

The fastest way is via the web UI. Start the server (`python backend/server.py`) and open `http://localhost:8000`. Click **Settings** in the top-right corner to open the LLM configuration dialog:

- Pick a **Provider** preset:
  - **Anthropic (Claude)** — official Anthropic endpoint, no base URL needed
  - **Custom (Anthropic-compatible)** — point at any Anthropic-protocol proxy and supply a model id
  - **OpenAI / DeepSeek / Moonshot / 智谱 GLM / Ollama / Custom (OpenAI-compatible)** — covers the OpenAI wire format
- The dialog auto-fills a sensible **Base URL** and **Model** for each preset; you can override either with your own value
- Paste your **API Key** (stored server-side only; never echoed back to the browser)
- Adjust **Temperature** if you want

The provider is persisted at `data/llm_config.json` and is hot-reloaded on every review job, so the next PR you submit uses the new provider.

### Switching providers via environment variables

If you'd rather configure at deploy time, the same fields can be set as env vars. The dashboard `data/llm_config.json` (if it exists) takes precedence over the env.

| Env var | Description |
|---------|-------------|
| `LLM_PROVIDER` | `anthropic` or `openai-compatible` |
| `LLM_API_KEY` | Provider API key. `ANTHROPIC_API_KEY` is still respected as a fallback for `anthropic`. |
| `LLM_MODEL` | Model id — free-form. `claude-sonnet-4-20250514`, `claude-3-opus-20240229`, `gpt-4o-mini`, `deepseek-chat`, `llama3.1`, fine-tunes, custom proxy ids, etc. |
| `LLM_BASE_URL` | Optional for `anthropic` (omit to use the official endpoint), required for `openai-compatible`. e.g. `https://api.openai.com/v1`, `http://localhost:11434/v1`, `https://your-litellm-proxy.example.com` |
| `LLM_TEMPERATURE` | `0.0` – `2.0` (default `0.2`) |

### Example: DeepSeek via env

```bash
export LLM_PROVIDER=openai-compatible
export LLM_API_KEY=sk-...
export LLM_BASE_URL=https://api.deepseek.com/v1
export LLM_MODEL=deepseek-chat
```

### Example: Local Ollama

```bash
export LLM_PROVIDER=openai-compatible
export LLM_BASE_URL=http://localhost:11434/v1
export LLM_MODEL=llama3.1
# Ollama ignores the key, but the field must be non-empty to be sent
export LLM_API_KEY=ollama
```

### Example: Anthropic-compatible proxy (LiteLLM, gateway, …)

```bash
export LLM_PROVIDER=anthropic
export LLM_API_KEY=sk-anything-the-proxy-expects
export LLM_BASE_URL=https://your-litellm-proxy.example.com
# Any model id your proxy exposes — LiteLLM accepts Anthropic model names,
# OpenAI model names, or any custom routing key
export LLM_MODEL=claude-3-5-sonnet-20241022
```

The agent will speak the Anthropic Messages protocol but talk to your proxy instead of `api.anthropic.com`. Useful for centralized key management, audit logging, on-prem deployments, or routing through Cloudflare / VPC.

### How requests are dispatched

| Provider type | Endpoint called | Auth header |
|---------------|-----------------|-------------|
| `anthropic` (no `base_url`) | Anthropic SDK → `https://api.anthropic.com` | handled by SDK |
| `anthropic` (with `base_url`) | Anthropic SDK → `{base_url}` | handled by SDK |
| `openai-compatible` | `POST {base_url}/chat/completions` | `Authorization: Bearer <key>` |

Review output structure is identical across providers — switching providers does not change the GitHub comment format, the score, or the issue categories the agent reports.

## How Multi-Turn Reasoning Works

The agent uses 3 sequential Claude API calls per review, each building on prior context:

| Turn | Focus | Approach |
|------|-------|----------|
| 1 | Bugs & Security | Deep scan: logic errors, null refs, injection risks, auth flaws |
| 2 | Performance & Quality | O(n^2) detection, N+1 queries, naming, complexity, test gaps |
| 3 | Structured Report | Synthesize all findings into final JSON review object |

## Review Output Structure

```json
{
  "summary": "High-level assessment",
  "severity": "critical|high|medium|low",
  "overall_score": 0-100,
  "approve": true|false,
  "bugs": [{"line": "file.py:42", "description": "...", "suggestion": "..."}],
  "security": [...],
  "performance": [...],
  "quality": [...],
  "positives": ["Good error handling in X", ...]
}
```

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | (required) | Anthropic API key (fallback for `LLM_API_KEY` when provider is `anthropic`) |
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `openai-compatible` |
| `LLM_API_KEY` | — | Provider API key. Overrides `ANTHROPIC_API_KEY` when set. |
| `LLM_MODEL` | `claude-sonnet-4-20250514` | Model id sent to the provider |
| `LLM_BASE_URL` | — | Required for `openai-compatible` providers |
| `LLM_TEMPERATURE` | `0.2` | Sampling temperature (0.0 – 2.0) |
| `GITHUB_TOKEN` | (required) | GitHub PAT with `repo` scope |
| `GITHUB_WEBHOOK_SECRET` | (required for webhooks) | HMAC-SHA256 webhook verification |
| `API_KEY` | — | Optional API key for endpoint authentication |
| `CORS_ORIGINS` | `http://localhost:8000,http://127.0.0.1:8000` | Comma-separated allowed CORS origins |
| `CLAUDE_MODEL` | `claude-sonnet-4-20250514` | Legacy alias for `LLM_MODEL` |
| `PR_REVIEW_MODE` | `full` | `full` / `diff-only` / `auto` |
| `PR_REVIEW_MAX_FILES` | `20` | Max files to include in context |
| `PR_REVIEW_MAX_FILE_CHARS` | `5000` | Max chars per file content |
| `PR_REVIEW_MAX_CONTEXT_CHARS` | `80000` | Total context size cap |
| `PR_REVIEW_FULL_CONTENT_THRESHOLD` | `500` | Max additions to fetch full content |
| `PR_REVIEW_CONTEXT_ENABLED` | `true` | Load CONTEXT.md from PR branch |
| `TOKEN_BUDGET_WEEKLY` | `2000000` | Weekly token budget for alerts |
| `TOKEN_BUDGET_ALERT_THRESHOLD` | `0.8` | Alert at 80% budget consumed |
| `LOG_LEVEL` | `INFO` | Logging level |
| `REDIS_URL` | — | Redis connection (required for production) |
| `WORKER_CONCURRENCY` | `10` | Max concurrent review tasks per worker |
| `MAX_QUEUE_SIZE` | `100` | Max pending reviews in queue |
| `ENVIRONMENT` | `production` | Environment identifier for logging |
| `SENTRY_DSN` | — | Optional Sentry error tracking DSN |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/review` | Start a new review job |
| `GET` | `/api/jobs` | List all jobs (filter: `?repo=`, `?days=`) |
| `GET` | `/api/jobs/{id}` | Get job status & result |
| `GET` | `/api/stats` | Aggregate statistics (filter: `?days=`) |
| `GET` | `/api/config/llm` | Read the current LLM provider config (api_key is masked) |
| `POST` | `/api/config/llm` | Persist the LLM provider config; leave `api_key` blank to keep the existing key |
| `GET` | `/api/metrics` | Combined quality + throughput + tokens |
| `GET` | `/api/metrics/tokens` | Weekly budget, daily breakdown, per-review stats |
| `GET` | `/api/metrics/quality` | Score distribution, severity, approval rate |
| `GET` | `/api/metrics/throughput` | Reviews/day, files reviewed |
| `POST` | `/webhook/github` | GitHub webhook handler |
| `GET` | `/api/health` | Health check (Redis, queue status) |
| `GET` | `/api/health/ready` | Kubernetes readiness probe |
| `GET` | `/api/health/live` | Kubernetes liveness probe |
| `GET` | `/metrics` | Prometheus metrics endpoint |

## Token Usage

Typical per-PR consumption:
- **Small PR** (< 5 files): ~15K–30K tokens
- **Medium PR** (5–20 files): ~40K–80K tokens
- **Large PR** (20+ files): ~80K–150K tokens

Use `PR_REVIEW_MODE=diff-only` to cut token usage ~50% for large PRs.

## Production Deployment

### Job Store

Three backends, auto-selected:

| Backend | Trigger | Persistence |
|---------|---------|-------------|
| `InMemoryJobStore` | (fallback) | Lost on restart |
| `FileJobStore` | Default | JSONL file at `data/jobs.jsonl` |
| `RedisJobStore` | Set `REDIS_URL` | Redis, with 7-day TTL, status indexing |

### Task Queue (arq + Redis)

For production workloads, the system uses an async task queue:

```bash
# Start the API server
python server.py

# Start worker(s) in separate processes
python scripts/start_worker.py
```

Features:
- Async task processing with automatic retries (3 attempts)
- Request deduplication prevents duplicate reviews
- Result caching (1 hour TTL based on PR head SHA)
- Graceful shutdown with active task tracking

### Process Manager

```bash
# Single worker (fine for < 100 PRs/week)
python server.py

# Multi-worker production with gunicorn
gunicorn server:app -w 4 -k uvicorn.workers.UvicornWorker
```

### Token Budget Monitoring

Set `TOKEN_BUDGET_WEEKLY` and check the dashboard for real-time budget tracking.
The stats bar shows a color-coded progress bar: green (< 50%), yellow (50–80%), red (> 80%).

## Security

### API Authentication

Set `API_KEY` environment variable to enable bearer token authentication on all `/api/*` endpoints. When set, requests must include:

```
Authorization: Bearer your-api-key-here
```

### Webhook Security

`GITHUB_WEBHOOK_SECRET` is **required** for webhook endpoints. Webhooks without a valid HMAC-SHA256 signature will be rejected. Generate a secret with:

```bash
openssl rand -hex 32
```

### CORS Configuration

Configure allowed origins with `CORS_ORIGINS` (comma-separated). Defaults to localhost only.

## GitHub Token Permissions

Minimum required scopes: `repo` (private repos) or `public_repo` (public). This allows reading PR diffs and posting review comments.

## Project-Specific Review Rules

Add `.pr-review-rules.md` to your repository root (or `.github/pr-review-rules.md`). The agent will inject these rules into the review prompt. Example:

```markdown
# PR Review Rules
- All API endpoints must validate input with Pydantic models
- Database queries must use parameterized queries
- All new functions must have type hints
- No console.log or print() in production code
```

Also supported: `CONTEXT.md` / `.pr-review-context.md` / `CONTRIBUTING.md` for general project context.

## Changelog

### v1.3.0 - Multi-Provider LLM Support

**New Features:**
- Dashboard **Settings** dialog to switch LLM provider, model, base URL, API key, and temperature without restarting the server
- Provider presets: Anthropic, OpenAI, DeepSeek, Moonshot (Kimi), Zhipu GLM, Ollama, Custom OpenAI-compatible, **Custom Anthropic-compatible** (LiteLLM, gateways, etc.)
- Custom model support: the **Model** field is free-form on both wire formats, so any model id works (claude-3-opus-*, claude-3-haiku-*, gpt-4o-*, deepseek-*, llama*, fine-tunes, proxy routing keys, …)
- Anthropic provider now accepts an optional **Base URL**, so you can route Anthropic-protocol traffic through a proxy while keeping the same review output
- Header status indicator reflects the active provider and shows a warning dot when no API key is set
- Added `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`, `LLM_TEMPERATURE` environment variables (dashboard `data/llm_config.json` still wins when present)

**Notes:**
- No breaking changes. Existing `ANTHROPIC_API_KEY` + `CLAUDE_MODEL` deployments continue to work without any edit.

### v1.2.0 - Production Readiness

**New Features:**
- Async task queue with arq + Redis for concurrent processing
- Request deduplication to prevent duplicate reviews
- Result caching with SHA-based keys (1 hour TTL)
- Prometheus metrics endpoint (`/metrics`)
- Kubernetes health probes (`/api/health/ready`, `/api/health/live`)
- Graceful shutdown with active task tracking
- Thread-safe singleton pattern for all managers
- Comprehensive unit tests for core modules

**Bug Fixes:**
- Fixed deprecated `datetime.utcnow()` (Python 3.12+ compatible)
- Fixed frontend polling memory leak using AbortController
- Fixed asyncio event loop handling with `asyncio.to_thread()`
- Enhanced RedisJobStore with status indexing for faster queries

**Infrastructure:**
- Added `backend/tasks.py` - arq task definitions
- Added `backend/dedup.py` - request deduplication
- Added `backend/cache.py` - result caching
- Added `backend/shutdown.py` - graceful shutdown
- Added `backend/metrics_middleware.py` - Prometheus middleware
- Added `backend/singleton.py` - thread-safe singleton decorator
- Added `scripts/start_worker.py` - Worker startup script
- Added comprehensive test suite in `tests/`

### v1.1.0 - Security & Reliability Improvements

**Security Fixes:**
- Fixed XSS vulnerability in frontend dashboard
- Added optional API key authentication (`API_KEY` env var)
- Webhook signature verification now required (no more bypass)
- CORS origins now configurable (defaults to localhost only)

**Bug Fixes:**
- Fixed job ID collision when submitting multiple reviews rapidly
- Fixed KeyError crash in stats endpoint with incomplete data
- Fixed fragile JSON parsing for Claude responses
- Fixed metrics dead code in throughput calculation
- Fixed token tracker week start normalization
- Updated deprecated `datetime.utcnow()` calls
- Fixed GitHub Actions workflow to install all dependencies

**New Features:**
- Added `CORS_ORIGINS` configuration
- Added `API_KEY` for endpoint authentication
- Added `backend/__init__.py` for proper module imports
