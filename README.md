# PR Review Agent

AI-powered pull request code review system using Claude API with multi-turn reasoning.
Designed for teams handling 50+ PRs/week with ~2M tokens weekly budget.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    PR Review Agent                        │
│                                                           │
│  GitHub PR ──► Agent ──► Turn 1: Bug & Security Scan     │
│                     │──► Turn 2: Performance & Quality    │
│                     │──► Turn 3: Structured JSON Report   │
│                     └──► Post Comment to GitHub PR        │
│                                                           │
│  FastAPI Server ──► REST API ──► Dashboard UI            │
│  GitHub Webhook ──► Auto-trigger on PR open/update/label │
│  Job Store ──► In-Memory / File (JSONL) / Redis          │
└──────────────────────────────────────────────────────────┘
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
- `ANTHROPIC_API_KEY` — from [console.anthropic.com](https://console.anthropic.com)
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

### 3c. GitHub Actions (Automated)

Copy `.github/workflows/pr-review.yml` to your repository. Add these secrets:
- `ANTHROPIC_API_KEY`
- `GITHUB_TOKEN`

Every PR open/sync/reopen triggers an automatic review.

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
| `ANTHROPIC_API_KEY` | (required) | Anthropic API key |
| `GITHUB_TOKEN` | (required) | GitHub PAT with `repo` scope |
| `GITHUB_WEBHOOK_SECRET` | — | HMAC-SHA256 webhook verification |
| `CLAUDE_MODEL` | `claude-sonnet-4-20250514` | Claude model to use |
| `PR_REVIEW_MODE` | `full` | `full` / `diff-only` / `auto` |
| `PR_REVIEW_MAX_FILES` | `20` | Max files to include in context |
| `PR_REVIEW_MAX_FILE_CHARS` | `5000` | Max chars per file content |
| `PR_REVIEW_MAX_CONTEXT_CHARS` | `80000` | Total context size cap |
| `PR_REVIEW_FULL_CONTENT_THRESHOLD` | `500` | Max additions to fetch full content |
| `PR_REVIEW_CONTEXT_ENABLED` | `true` | Load CONTEXT.md from PR branch |
| `TOKEN_BUDGET_WEEKLY` | `2000000` | Weekly token budget for alerts |
| `TOKEN_BUDGET_ALERT_THRESHOLD` | `0.8` | Alert at 80% budget consumed |
| `LOG_LEVEL` | `INFO` | Logging level |
| `REDIS_URL` | — | Redis connection (optional, for prod) |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/review` | Start a new review job |
| `GET` | `/api/jobs` | List all jobs (filter: `?repo=`, `?days=`) |
| `GET` | `/api/jobs/{id}` | Get job status & result |
| `GET` | `/api/stats` | Aggregate statistics (filter: `?days=`) |
| `GET` | `/api/metrics` | Combined quality + throughput + tokens |
| `GET` | `/api/metrics/tokens` | Weekly budget, daily breakdown, per-review stats |
| `GET` | `/api/metrics/quality` | Score distribution, severity, approval rate |
| `GET` | `/api/metrics/throughput` | Reviews/day, files reviewed |
| `POST` | `/webhook/github` | GitHub webhook handler |
| `GET` | `/api/health` | Health check |

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
| `RedisJobStore` | Set `REDIS_URL` | Redis, with 7-day TTL |

### Process Manager

```bash
# Single worker (fine for < 100 PRs/week)
python server.py

# Multi-worker production
gunicorn server:app -w 4 -k uvicorn.workers.UvicornWorker
```

### Token Budget Monitoring

Set `TOKEN_BUDGET_WEEKLY` and check the dashboard for real-time budget tracking.
The stats bar shows a color-coded progress bar: green (< 50%), yellow (50–80%), red (> 80%).

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
