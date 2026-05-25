"""
PR Review Agent - Multi-turn Claude-powered code review system
"""
import os
import json
import re
import time
import logging
from typing import Optional
from github import Github, GithubException
import anthropic as anthropic_module
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert senior software engineer conducting thorough code reviews.
Your task is to analyze pull request changes and provide structured, actionable feedback.

For each review, you MUST:
1. Identify potential Bugs - logic errors, null pointer risks, edge cases, incorrect assumptions
2. Flag Security Vulnerabilities - injection risks, auth issues, exposed secrets, insecure dependencies
3. Spot Performance Issues - O(n²) algorithms, N+1 queries, memory leaks, blocking calls
4. Note Code Quality - naming, complexity, duplication, missing tests, poor error handling
5. Provide Positive Feedback - acknowledge good patterns and well-written code

Output your review as valid JSON with this exact structure:
{
  "summary": "2-3 sentence high-level assessment",
  "severity": "critical|high|medium|low",
  "bugs": [{"line": "file:line", "description": "...", "suggestion": "..."}],
  "security": [{"line": "file:line", "description": "...", "suggestion": "..."}],
  "performance": [{"line": "file:line", "description": "...", "suggestion": "..."}],
  "quality": [{"line": "file:line", "description": "...", "suggestion": "..."}],
  "positives": ["..."],
  "overall_score": 0-100,
  "approve": true|false
}

Be precise, technical, and constructive. Reference specific line numbers when possible."""

# --- Context limits from env ---
MAX_FILES = int(os.getenv("PR_REVIEW_MAX_FILES", "20"))
MAX_FILE_CONTENT_CHARS = int(os.getenv("PR_REVIEW_MAX_FILE_CHARS", "5000"))
MAX_TOTAL_CONTEXT_CHARS = int(os.getenv("PR_REVIEW_MAX_CONTEXT_CHARS", "80000"))
REVIEW_MODE = os.getenv("PR_REVIEW_MODE", "full")
FULL_CONTENT_THRESHOLD = int(os.getenv("PR_REVIEW_FULL_CONTENT_THRESHOLD", "500"))


def _should_fetch_full_content(file_info: dict, total_additions: int) -> bool:
    """Decide whether to fetch full file content based on REVIEW_MODE."""
    if file_info.get("status") == "removed":
        return False
    if REVIEW_MODE == "diff-only":
        return False
    if REVIEW_MODE == "full":
        return file_info["additions"] < FULL_CONTENT_THRESHOLD
    if REVIEW_MODE == "auto":
        if total_additions > 1000:
            return False
        return file_info["additions"] < FULL_CONTENT_THRESHOLD
    return True


class PRReviewAgent:
    def __init__(self, github_token: str, anthropic_api_key: str):
        self.gh = Github(github_token)
        self.client = anthropic_module.Anthropic(api_key=anthropic_api_key, timeout=120)
        self.model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
        self.conversation_history = []
        self.token_usage = {"input": 0, "output": 0}
        logger.info(f"PRReviewAgent initialized — model: {self.model}")

    def _call_claude(self, messages: list, max_tokens: int = 4096) -> str:
        """Make a call to Claude API with conversation history and retry logic."""
        return self._call_claude_inner(messages, max_tokens)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type((
            anthropic_module.RateLimitError,
            anthropic_module.APIConnectionError,
            anthropic_module.InternalServerError,
            anthropic_module.APITimeoutError,
        )),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _call_claude_inner(self, messages: list, max_tokens: int = 4096) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        usage = response.usage
        self.token_usage["input"] += usage.input_tokens
        self.token_usage["output"] += usage.output_tokens
        return response.content[0].text

    def _get_pr_context(self, repo_name: str, pr_number: int) -> dict:
        """Fetch PR metadata, diff, and file contents from GitHub."""
        repo = self.gh.get_repo(repo_name)
        pr = repo.get_pull(pr_number)

        files_data = []
        for f in pr.get_files():
            file_info = {
                "filename": f.filename,
                "status": f.status,
                "additions": f.additions,
                "deletions": f.deletions,
                "patch": f.patch or "(binary file or too large)",
            }
            if _should_fetch_full_content(file_info, pr.additions):
                try:
                    content = repo.get_contents(f.filename, ref=pr.head.sha)
                    if hasattr(content, "decoded_content"):
                        full = content.decoded_content.decode("utf-8", errors="replace")
                        file_info["full_content"] = full[:MAX_FILE_CONTENT_CHARS]
                except Exception:
                    pass
            files_data.append(file_info)

        # Get recent commit messages for context
        commits = list(pr.get_commits())[-5:]
        commit_messages = [c.commit.message.split("\n")[0] for c in commits]

        return {
            "repo": repo_name,
            "pr_number": pr_number,
            "title": pr.title,
            "description": pr.body or "(no description)",
            "author": pr.user.login,
            "base_branch": pr.base.ref,
            "head_branch": pr.head.ref,
            "changed_files": len(files_data),
            "total_additions": pr.additions,
            "total_deletions": pr.deletions,
            "commit_messages": commit_messages,
            "files": files_data,
        }

    def _load_project_context(self, repo_name: str, pr_number: int) -> str:
        """Try to load project-specific context from the PR's head branch."""
        context_enabled = os.getenv("PR_REVIEW_CONTEXT_ENABLED", "true").lower() == "true"
        if not context_enabled:
            return ""
        try:
            from context import ProjectContext
            repo = self.gh.get_repo(repo_name)
            pr = repo.get_pull(pr_number)
            return ProjectContext(repo, pr).context_text
        except Exception as e:
            logger.debug(f"Could not load project context: {e}")
            return ""

    def _select_and_format_files(self, pr_context: dict) -> list:
        """Select and format files for review, respecting limits."""
        # Sort: added/modified first, then by change size (descending)
        files = sorted(
            pr_context["files"],
            key=lambda f: (
                0 if f["status"] in ("added", "modified") else 1,
                -(f.get("additions", 0) + f.get("deletions", 0)),
            ),
        )
        return files[:MAX_FILES]

    def _format_file_for_review(self, f: dict) -> str:
        """Format a single file for the review prompt with smart truncation."""
        lines = [f"\n### {f['filename']} ({f['status']})"]
        full = f.get("full_content", "")

        if full:
            full_len = len(full)
            if full_len <= MAX_FILE_CONTENT_CHARS:
                # Small file: include all
                lines.append(f"Full content:\n```\n{full}\n```")
            else:
                # Smart truncation: first 2/3 + last 1/3
                split = int(MAX_FILE_CONTENT_CHARS * 0.67)
                head = full[:split]
                tail = full[-(MAX_FILE_CONTENT_CHARS - split):]
                lines.append(f"Full content (truncated, {full_len} chars total):\n```\n{head}\n\n... [truncated] ...\n\n{tail}\n```")

        lines.append(f"Diff:\n```diff\n{f['patch']}\n```")
        return "\n".join(lines)

    def _multi_turn_review(self, pr_context: dict) -> dict:
        """
        Multi-turn conversation with Claude for deep analysis:
        Turn 1: Initial scan for bugs and security issues
        Turn 2: Performance and architectural analysis
        Turn 3: Final structured report generation
        """
        self.conversation_history = []

        # Select files for review
        selected_files = self._select_and_format_files(pr_context)

        # Load project-specific context
        project_context = self._load_project_context(
            pr_context["repo"], pr_context["pr_number"]
        )

        # --- Turn 1: Bug & Security Scan ---
        turn1_parts = []
        if project_context:
            turn1_parts.append(project_context)

        turn1_parts.append(f"""PR Context:
Title: {pr_context['title']}
Description: {pr_context['description']}
Author: {pr_context['author']}
Files changed: {pr_context['changed_files']} (+{pr_context['total_additions']} -{pr_context['total_deletions']})
Recent commits: {json.dumps(pr_context['commit_messages'])}
""")

        total_chars = sum(len(p) for p in turn1_parts)
        file_chars = 0

        for f in selected_files:
            formatted = self._format_file_for_review(f)
            if total_chars + len(formatted) > MAX_TOTAL_CONTEXT_CHARS:
                logger.warning(
                    f"Context limit ({MAX_TOTAL_CONTEXT_CHARS} chars) reached; "
                    f"stopping at {len([p for p in turn1_parts if p.startswith('\\n###')])} files"
                )
                break
            turn1_parts.append(formatted)
            file_chars += len(formatted)

        turn1_parts.append(
            "\nFirst, focus ONLY on identifying bugs and security vulnerabilities. Be thorough and specific."
        )

        turn1_content = "\n".join(turn1_parts)
        logger.info(
            f"Turn 1 context: {len(selected_files)} files, "
            f"~{total_chars + file_chars} chars, project_context={'yes' if project_context else 'no'}"
        )

        self.conversation_history.append({"role": "user", "content": turn1_content})
        turn1_response = self._call_claude(self.conversation_history)
        self.conversation_history.append({"role": "assistant", "content": turn1_response})
        logger.info("Turn 1 complete - bugs/security analysis done")

        # --- Turn 2: Performance & Code Quality ---
        turn2_content = "Now analyze the same code for performance issues, code quality problems, and architectural concerns. Also note any positive patterns you see."
        self.conversation_history.append({"role": "user", "content": turn2_content})
        turn2_response = self._call_claude(self.conversation_history)
        self.conversation_history.append({"role": "assistant", "content": turn2_response})
        logger.info("Turn 2 complete - performance/quality analysis done")

        # --- Turn 3: Generate Final Structured Report ---
        turn3_content = """Based on your complete analysis above, now generate the final structured JSON review report.
Include all findings from both turns. Return ONLY valid JSON, no markdown fences, no extra text."""
        self.conversation_history.append({"role": "user", "content": turn3_content})
        turn3_response = self._call_claude(self.conversation_history, max_tokens=2048)
        logger.info("Turn 3 complete - structured report generated")

        # Parse JSON response
        try:
            clean = turn3_response.strip()
            # Try to extract JSON from markdown code fences
            json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', clean, re.DOTALL)
            if json_match:
                clean = json_match.group(1).strip()
            review_data = json.loads(clean.strip())
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse failed: {e}, using fallback")
            review_data = {
                "summary": turn3_response[:500],
                "severity": "medium",
                "bugs": [],
                "security": [],
                "performance": [],
                "quality": [],
                "positives": [],
                "overall_score": 50,
                "approve": False,
                "_raw_response": turn3_response,
            }

        review_data["token_usage"] = dict(self.token_usage)
        review_data["turns"] = 3
        return review_data

    def _format_github_comment(self, review: dict, pr_context: dict) -> str:
        """Format the review as a rich GitHub comment."""
        severity_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(
            review.get("severity", "medium"), "⚪"
        )
        score = review.get("overall_score", 0)
        score_bar = "█" * (score // 10) + "░" * (10 - score // 10)

        lines = [
            f"## 🤖 AI Code Review — PR #{pr_context['pr_number']}",
            "",
            f"> **{review.get('summary', 'Review completed.')}**",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Severity | {severity_emoji} {review.get('severity', 'N/A').upper()} |",
            f"| Score | `{score_bar}` {score}/100 |",
            f"| Files Reviewed | {pr_context['changed_files']} |",
            f"| Tokens Used | {review['token_usage']['input'] + review['token_usage']['output']:,} |",
            f"| Analysis Turns | {review.get('turns', 3)} |",
            "",
        ]

        def add_section(title, emoji, items, key="description"):
            if not items:
                return
            lines.append(f"### {emoji} {title} ({len(items)})")
            lines.append("")
            for item in items:
                loc = f"`{item.get('line', 'N/A')}`" if item.get("line") else ""
                lines.append(f"- {loc} **{item.get(key, item if isinstance(item, str) else '')}")
                if item.get("suggestion"):
                    lines.append(f"  > 💡 {item['suggestion']}")
            lines.append("")

        add_section("Bugs Found", "🐛", review.get("bugs", []))
        add_section("Security Issues", "🔒", review.get("security", []))
        add_section("Performance Issues", "⚡", review.get("performance", []))
        add_section("Code Quality", "📝", review.get("quality", []))

        if review.get("positives"):
            lines.append("### ✅ Positives")
            lines.append("")
            for p in review["positives"]:
                lines.append(f"- {p}")
            lines.append("")

        verdict = (
            "✅ **APPROVED** — Looks good to merge!"
            if review.get("approve")
            else "🔄 **CHANGES REQUESTED** — Please address the issues above."
        )
        lines.append("---")
        lines.append(f"### Verdict: {verdict}")
        lines.append("")
        lines.append(
            f"*Generated by PR Review Agent using Claude API · "
            f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}*"
        )

        return "\n".join(lines)

    def review_pr(self, repo_name: str, pr_number: int, post_comment: bool = True) -> dict:
        """Full pipeline: fetch PR → multi-turn review → post comment."""
        logger.info(f"Starting review for {repo_name}#{pr_number}")

        pr_context = self._get_pr_context(repo_name, pr_number)
        logger.info(f"Fetched PR: '{pr_context['title']}' ({pr_context['changed_files']} files)")

        review = self._multi_turn_review(pr_context)

        comment_body = self._format_github_comment(review, pr_context)

        if post_comment:
            try:
                repo = self.gh.get_repo(repo_name)
                pr = repo.get_pull(pr_number)
                pr.create_issue_comment(comment_body)
                logger.info(f"Posted review comment to PR #{pr_number}")
            except GithubException as e:
                logger.error(f"Failed to post comment: {e}")

        result = {
            "pr_number": pr_number,
            "repo": repo_name,
            "title": pr_context["title"],
            "review": review,
            "comment": comment_body,
            "token_usage": self.token_usage,
        }
        return result
