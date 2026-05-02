"""Token usage tracking with weekly budget and alert thresholds."""
import os
import time
import logging
from datetime import datetime, timedelta
from typing import Optional

from job_store import get_job_store

logger = logging.getLogger(__name__)

WEEKLY_BUDGET = int(os.getenv("TOKEN_BUDGET_WEEKLY", "2000000"))
ALERT_THRESHOLD = float(os.getenv("TOKEN_BUDGET_ALERT_THRESHOLD", "0.8"))


class TokenTracker:
    """Tracks token consumption per review, per day, per week."""

    def __init__(self, job_store=None):
        self._store = job_store or get_job_store()
        self.weekly_budget = WEEKLY_BUDGET
        self.alert_threshold = ALERT_THRESHOLD

    def _week_start(self, dt: Optional[datetime] = None) -> datetime:
        dt = dt or datetime.utcnow()
        return dt - timedelta(days=dt.weekday())

    def _all_done_jobs(self) -> list:
        return self._store.list_by_status("done")

    def weekly_usage(self, week_start: Optional[datetime] = None) -> dict:
        """Return {used, budget, remaining, alert_level, alert_triggered} for the week."""
        week_start = week_start or self._week_start()
        week_start_ts = week_start.timestamp()
        used = 0
        for job in self._all_done_jobs():
            created = job.get("_created_ts", 0)
            if created >= week_start_ts:
                result = job.get("result", {})
                usage = result.get("token_usage", {})
                used += usage.get("input", 0) + usage.get("output", 0)

        remaining = self.weekly_budget - used
        pct = used / self.weekly_budget if self.weekly_budget > 0 else 0

        if pct >= 0.9:
            level = "critical"
        elif pct >= self.alert_threshold:
            level = "warning"
        else:
            level = "normal"

        return {
            "used": used,
            "budget": self.weekly_budget,
            "remaining": max(remaining, 0),
            "used_pct": round(pct * 100, 1),
            "alert_level": level,
            "alert_triggered": pct >= self.alert_threshold,
        }

    def daily_usage(self, days: int = 7) -> list[dict]:
        """Return daily token breakdown for the last `days` days."""
        now = datetime.utcnow()
        daily = {}
        for i in range(days):
            day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            daily[day] = 0

        for job in self._all_done_jobs():
            created = job.get("created_at", "")
            if created:
                day = created[:10]
                if day in daily:
                    result = job.get("result", {})
                    usage = result.get("token_usage", {})
                    daily[day] += usage.get("input", 0) + usage.get("output", 0)

        return [
            {"date": d, "tokens": daily[d]}
            for d in sorted(daily.keys())
        ]

    def per_review_stats(self) -> dict:
        """Return {avg, median, p95, max, total_reviews} token usage."""
        tokens_list = []
        for job in self._all_done_jobs():
            result = job.get("result", {})
            usage = result.get("token_usage", {})
            total = usage.get("input", 0) + usage.get("output", 0)
            if total > 0:
                tokens_list.append(total)

        if not tokens_list:
            return {"avg": 0, "median": 0, "p95": 0, "max": 0, "total_reviews": 0}

        tokens_list.sort()
        n = len(tokens_list)
        return {
            "avg": round(sum(tokens_list) / n),
            "median": tokens_list[n // 2],
            "p95": tokens_list[int(n * 0.95)] if n > 1 else tokens_list[0],
            "max": tokens_list[-1],
            "total_reviews": n,
        }

    def record(self, repo: str, pr_number: int, input_tokens: int, output_tokens: int, model: str):
        """Persist a token consumption record. Called after each review."""
        record_id = f"tokens:{repo}#{pr_number}:{int(time.time())}"
        self._store.create(
            record_id,
            {
                "repo": repo,
                "pr_number": pr_number,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "model": model,
                "_created_ts": time.time(),
                "created_at": datetime.utcnow().isoformat(),
                "type": "token_record",
            },
        )


# Singleton
_tracker: Optional[TokenTracker] = None


def get_token_tracker() -> TokenTracker:
    global _tracker
    if _tracker is None:
        _tracker = TokenTracker()
    return _tracker
