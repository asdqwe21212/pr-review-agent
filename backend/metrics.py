"""Review quality metrics – aggregates over completed reviews."""
import logging
from datetime import datetime, timedelta
from typing import Optional

from job_store import get_job_store
from token_tracker import get_token_tracker

logger = logging.getLogger(__name__)


class ReviewMetrics:
    """Compute aggregate quality and throughput metrics."""

    def __init__(self, job_store=None):
        self._store = job_store or get_job_store()

    def _done_jobs(self, days: Optional[int] = 7) -> list:
        jobs = self._store.list_by_status("done")
        if days is not None and days > 0:
            since = (datetime.utcnow() - timedelta(days=days)).timestamp()
            jobs = [j for j in jobs if j.get("_created_ts", 0) >= since]
        return jobs

    def quality_summary(self, days: int = 7) -> dict:
        """Aggregate quality metrics for completed reviews."""
        done = self._done_jobs(days)
        if not done:
            return {
                "total_reviews": 0,
                "avg_score": 0,
                "score_distribution": {},
                "severity_distribution": {},
                "approval_rate": 0,
                "avg_issues_per_review": 0,
            }

        scores = []
        severities = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        approvals = 0
        total_issues = 0

        for job in done:
            result = job.get("result", {})
            review = result.get("review", {})
            score = review.get("overall_score", 0)
            if isinstance(score, (int, float)):
                scores.append(score)

            sev = review.get("severity", "low")
            if sev in severities:
                severities[sev] += 1

            if review.get("approve"):
                approvals += 1

            bugs = len(review.get("bugs", []))
            security = len(review.get("security", []))
            perf = len(review.get("performance", []))
            quality = len(review.get("quality", []))
            total_issues += bugs + security + perf + quality

        n = len(done)

        # Score distribution (buckets of 20)
        distribution = {"0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}
        for s in scores:
            if s <= 20:
                distribution["0-20"] += 1
            elif s <= 40:
                distribution["21-40"] += 1
            elif s <= 60:
                distribution["41-60"] += 1
            elif s <= 80:
                distribution["61-80"] += 1
            else:
                distribution["81-100"] += 1

        return {
            "total_reviews": n,
            "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "score_distribution": distribution,
            "severity_distribution": severities,
            "approval_rate": round(approvals / n * 100, 1),
            "avg_issues_per_review": round(total_issues / n, 1),
            "time_window_days": days,
        }

    def throughput(self, days: int = 7) -> dict:
        """Throughput metrics: reviews per day, avg time per review."""
        done = self._done_jobs(days)
        if not done:
            return {"reviews_last_7_days": 0, "avg_reviews_per_day": 0, "avg_files_per_review": 0}

        daily_counts = {}
        total_files = 0
        for job in done:
            created = job.get("created_at", "")[:10]
            daily_counts[created] = daily_counts.get(created, 0) + 1
            result = job.get("result", {})
            total_files += result.get("title", "") and 0 or 0  # can't get files from result easily

        # Count files from job metadata if available
        for job in done:
            files = job.get("_files_changed") or 0
            total_files += files
        if total_files == 0:
            # Fallback: just count reviews
            total_files = len(done)

        n = len(done)
        active_days = len(daily_counts) or 1

        return {
            "reviews_last_n_days": n,
            "time_window_days": days,
            "avg_reviews_per_day": round(n / active_days, 1),
            "active_days": active_days,
            "total_files_reviewed": total_files,
        }

    def combined_report(self, days: int = 7) -> dict:
        """Full combined metrics payload for the dashboard."""
        token_tracker = get_token_tracker()
        return {
            "quality": self.quality_summary(days),
            "throughput": self.throughput(days),
            "tokens": {
                "weekly": token_tracker.weekly_usage(),
                "daily": token_tracker.daily_usage(days),
                "per_review": token_tracker.per_review_stats(),
            },
        }


# Singleton
_metrics: Optional[ReviewMetrics] = None


def get_review_metrics() -> ReviewMetrics:
    global _metrics
    if _metrics is None:
        _metrics = ReviewMetrics()
    return _metrics
