"""Project context injection – loads repo-specific conventions from the PR branch."""
import logging

logger = logging.getLogger(__name__)

CONTEXT_FILES = [
    "CONTEXT.md",
    ".pr-review-context.md",
    "CONTRIBUTING.md",
]

CUSTOM_RULES_FILES = [
    ".pr-review-rules.md",
    ".github/pr-review-rules.md",
]


class ProjectContext:
    """Load project-specific context and custom review rules from the PR branch."""

    def __init__(self, repo, pr, max_chars: int = 2000):
        self.context_text = ""
        self.custom_rules = ""
        self._load(repo, pr, max_chars)

    def _load(self, repo, pr, max_chars: int):
        parts = []

        # Load context files
        for filename in CONTEXT_FILES:
            content = self._fetch_file(repo, pr, filename)
            if content:
                truncated = content[:max_chars]
                parts.append(
                    f"## Project-Specific Context (from {filename}):\n{truncated}"
                )
                break  # Use the first context file found

        # Load custom review rules
        for filename in CUSTOM_RULES_FILES:
            content = self._fetch_file(repo, pr, filename)
            if content:
                self.custom_rules = content
                parts.append(
                    f"\n## IMPORTANT: Project-Specific Review Rules (from {filename}):\n{content}\n"
                    "You MUST apply these rules in your review."
                )
                break

        if parts:
            self.context_text = "\n\n".join(parts)
            logger.info(
                f"Loaded project context ({len(self.context_text)} chars) from branch {pr.head.ref}"
            )

    @staticmethod
    def _fetch_file(repo, pr, filename: str) -> str:
        try:
            content = repo.get_contents(filename, ref=pr.head.sha)
            if hasattr(content, "decoded_content"):
                return content.decoded_content.decode("utf-8", errors="replace")
        except Exception:
            pass
        return ""
