"""
GitHub Webhook Handler - Auto-trigger reviews when PRs are opened/updated
Add this as a GitHub webhook: POST /webhook/github
"""
import os
import hmac
import hashlib
import asyncio
import logging
import secrets
from fastapi import Request, HTTPException
from fastapi.routing import APIRouter

webhook_router = APIRouter()
logger = logging.getLogger(__name__)

WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")


def verify_signature(payload: bytes, signature: str) -> bool:
    if not WEBHOOK_SECRET:
        logger.warning("GITHUB_WEBHOOK_SECRET not configured - rejecting webhook for security")
        return False
    if not signature:
        return False
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@webhook_router.post("/webhook/github")
async def github_webhook(request: Request):
    """Handle GitHub PR webhook events."""
    payload_bytes = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not verify_signature(payload_bytes, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    event = request.headers.get("X-GitHub-Event")
    payload = await request.json()

    if event == "pull_request":
        action = payload.get("action")
        if action in ("opened", "synchronize", "reopened"):
            pr_number = payload["pull_request"]["number"]
            repo_name = payload["repository"]["full_name"]

            from job_store import get_job_store
            from server import run_review_job
            from datetime import datetime, timezone

            job_store = get_job_store()
            now = datetime.now(timezone.utc)
            job_id = f"webhook_{pr_number}_{int(now.timestamp())}_{secrets.token_hex(4)}"
            job_data = {
                "job_id": job_id,
                "status": "pending",
                "created_at": now.isoformat(),
                "repo": repo_name,
                "pr_number": pr_number,
                "result": None,
                "error": None,
                "trigger": "webhook",
                "_created_ts": now.timestamp(),
            }
            job_store.create(job_id, job_data)
            asyncio.create_task(run_review_job(job_id, repo_name, pr_number, post_comment=True))
            logger.info(f"Webhook triggered review — {repo_name}#{pr_number} (job: {job_id})")
            return {"status": "review_started", "job_id": job_id}

    return {"status": "ignored", "event": event}
