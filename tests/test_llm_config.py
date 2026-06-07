"""LLM 配置测试"""
from pathlib import Path

from backend.llm_config import LLMConfig, LLMConfigStore


def test_default_config_falls_back_to_environment(monkeypatch, temp_dir):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("CLAUDE_MODEL", "claude-test-model")

    store = LLMConfigStore(temp_dir / "llm_config.json")

    config = store.load_effective()
    assert config.provider == "anthropic"
    assert config.api_key == "sk-ant-test"
    assert config.model == "claude-test-model"


def test_save_openai_compatible_config_masks_secret(temp_dir):
    store = LLMConfigStore(temp_dir / "llm_config.json")

    saved = store.save(
        LLMConfig(
            provider="openai-compatible",
            api_key="sk-openai-test",
            model="gpt-4o-mini",
            base_url="https://api.openai.com/v1",
            temperature=0.2,
        )
    )

    assert saved.provider == "openai-compatible"
    assert saved.api_key == "sk-openai-test"

    public_config = store.load_public()
    assert public_config["provider"] == "openai-compatible"
    assert public_config["model"] == "gpt-4o-mini"
    assert public_config["base_url"] == "https://api.openai.com/v1"
    assert public_config["api_key"] == ""
    assert public_config["api_key_set"] is True


def test_blank_api_key_preserves_existing_secret(temp_dir):
    store = LLMConfigStore(temp_dir / "llm_config.json")
    store.save(
        LLMConfig(
            provider="openai-compatible",
            api_key="sk-existing",
            model="gpt-4o-mini",
            base_url="https://api.openai.com/v1",
        )
    )

    updated = store.save(
        LLMConfig(
            provider="openai-compatible",
            api_key="",
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
        ),
        preserve_existing_secret=True,
    )

    assert updated.api_key == "sk-existing"
    assert updated.model == "deepseek-chat"
    assert updated.base_url == "https://api.deepseek.com/v1"