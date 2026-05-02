#!/usr/bin/env python3
"""
CLI tool for running PR reviews directly from command line.
Usage: python cli.py --repo owner/repo --pr 42
"""
import os
import sys
import json
import argparse
import logging
from agent import PRReviewAgent
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="AI-powered PR Code Review Agent")
    parser.add_argument("--repo", required=True, help="GitHub repo in format owner/repo")
    parser.add_argument("--pr", required=True, type=int, help="PR number to review")
    parser.add_argument("--no-comment", action="store_true", help="Skip posting GitHub comment")
    parser.add_argument("--output", help="Save full result to JSON file")
    args = parser.parse_args()

    github_token = os.getenv("GITHUB_TOKEN")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")

    if not github_token:
        print("❌ Error: GITHUB_TOKEN environment variable not set")
        sys.exit(1)
    if not anthropic_key:
        print("❌ Error: ANTHROPIC_API_KEY environment variable not set")
        sys.exit(1)

    print(f"🔍 Starting review for {args.repo}#{args.pr}...")

    agent = PRReviewAgent(github_token, anthropic_key)
    result = agent.review_pr(args.repo, args.pr, post_comment=not args.no_comment)

    review = result["review"]
    print(f"\n{'='*60}")
    print(f"✅ Review Complete: {result['title']}")
    print(f"{'='*60}")
    print(f"📊 Overall Score: {review.get('overall_score', 'N/A')}/100")
    print(f"🚨 Severity: {review.get('severity', 'N/A').upper()}")
    print(f"🐛 Bugs: {len(review.get('bugs', []))}")
    print(f"🔒 Security Issues: {len(review.get('security', []))}")
    print(f"⚡ Performance Issues: {len(review.get('performance', []))}")
    print(f"📝 Quality Issues: {len(review.get('quality', []))}")
    print(f"✅ Approve: {'Yes' if review.get('approve') else 'No'}")
    print(f"🔢 Tokens used: {result['token_usage']['input'] + result['token_usage']['output']:,}")
    print(f"\n📋 Summary: {review.get('summary', '')}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n💾 Full result saved to {args.output}")

    if not args.no_comment:
        print(f"\n💬 Review comment posted to GitHub PR #{args.pr}")


if __name__ == "__main__":
    main()
