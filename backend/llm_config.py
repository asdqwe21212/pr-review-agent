"""LLM provider configuration."""
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

Provider = Literal["anthropic", "openai-compatible"]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "data" / "llm_config.json"


@dataclass
class LLMConfig:
    provider: Provider = "anthropic"
    api_key: str = ""
    model: str = "claude-sonnet-4-20250514"
    base_url: str = ""
    temperature: float = 0.2

    def normalized(self) -> "LLMConfig":
        provider = self.provider if self.provider in ("anthropic", "openai-compatible") else "anthropic"
        model = (self.model or "").strip()
        base_url = (self.base_url or "").strip().rstrip("/")
        api_key = self.api_key or ""
        temperature = min(max(float(self.temperature), 0.0), 2.0)

        if not model:
            model = "claude-sonnet-4-20250514" if provider == "anthropic" else "gpt-4o-mini"

        if provider == "openai-compatible" and not base_url:
            base_url = "https://api.openai.com/v1"

        return LLMConfig(
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=base_url,
            temperature=temperature,
        )


class LLMConfigStore:
    def __init__(self, path: Path = DEFAULT_CONFIG_PATH):
        self.path = path

    def load_saved(self) -> LLMConfig | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return LLMConfig(**data).normalized()

    def load_effective(self) -> LLMConfig:
        saved = self.load_saved()
        if saved:
            return saved

        provider = os.getenv("LLM_PROVIDER", "anthropic")
        api_key = os.getenv("LLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY", "")
        model = os.getenv("LLM_MODEL") or os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
        base_url = os.getenv("LLM_BASE_URL", "")
        temperature = float(os.getenv("LLM_TEMPERATURE", "0.2"))
        return LLMConfig(provider=provider, api_key=api_key, model=model, base_url=base_url, temperature=temperature).normalized()

    def save(self, config: LLMConfig, preserve_existing_secret: bool = False) -> LLMConfig:
        normalized = config.normalized()
        if preserve_existing_secret and not normalized.api_key:
            existing = self.load_saved()
            if existing:
                normalized.api_key = existing.api_key

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(asdict(normalized), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return normalized

    def load_public(self) -> dict:
        config = self.load_effective()
        data = asdict(config)
        data["api_key_set"] = bool(config.api_key)
        data["api_key"] = ""
        return data


def get_llm_config_store() -> LLMConfigStore:
    return LLMConfigStore()