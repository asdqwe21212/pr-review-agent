# 🤖 PR Review Agent

AI-powered pull request code review system using Claude API with multi-turn reasoning.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     PR Review Agent                      │
│                                                          │
│  GitHub PR ──► Agent ──► Turn 1: Bug & Security Scan    │
│                     │──► Turn 2: Performance & Quality   │
│                     │──► Turn 3: Structured JSON Report  │
│                     └──► Post Comment to GitHub PR       │
│                                                          │
│  FastAPI Server ──► REST API ──► React Dashboard UI     │
│  GitHub Webhook ──► Auto-trigger on PR open/update      │
└─────────────────────────────────────────────────────────┘
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
export $(cat ../config/.env | xargs)

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
Copy `.github/workflows/pr-review.yml` to your repository.

Add secrets in GitHub repo settings:
- `ANTHROPIC_API_KEY`
- (GITHUB_TOKEN is provided automatically)

Every PR open/update will trigger an automatic review.

## How Multi-Turn Reasoning Works

The agent uses 3 sequential Claude API calls per review:

| Turn | Focus | Approach |
|------|-------|----------|
| 1 | Bugs & Security | Deep scan of logic errors, null refs, injection risks, auth flaws |
| 2 | Performance & Quality | O(n²) algos, N+1 queries, naming, complexity, missing tests |
| 3 | Structured Report | Synthesize all findings into final JSON review object |

Each turn builds on the previous conversation, enabling deeper chain-of-thought reasoning than a single prompt.

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

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/review` | Start a new review job |
| GET | `/api/jobs` | List all jobs |
| GET | `/api/jobs/{id}` | Get job status & result |
| GET | `/api/stats` | Aggregate statistics |
| POST | `/webhook/github` | GitHub webhook handler |
| GET | `/api/health` | Health check |

## Token Usage

Typical usage per PR review:
- **Small PR** (< 5 files): ~15K–30K tokens
- **Medium PR** (5–20 files): ~40K–80K tokens  
- **Large PR** (20+ files): ~80K–150K tokens

At 50 PRs/week, estimated weekly consumption: ~1.5M–3M tokens.

## GitHub Token Permissions

Minimum required scopes:
- `repo` (for private repos) or `public_repo` (for public)
- This allows reading PRs and posting comments

## Production Deployment

For production, replace the in-memory job store with Redis:

```python
# In server.py, use redis-py:
import redis
r = redis.Redis()
r.setex(f"job:{job_id}", 3600, json.dumps(job_data))
```

Use a process manager like `gunicorn` with `uvicorn` workers:
```bash
gunicorn server:app -w 4 -k uvicorn.workers.UvicornWorker
```
