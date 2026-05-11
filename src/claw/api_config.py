"""API configuration module for CodeAgent.

Handles API configuration discovery and resolution.
Supports both environment variables and config files.
Supports OpenAI-compatible and Anthropic-native formats.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from enum import Enum

from .hook_policy import RuntimeBase


class APIProvider(Enum):
    """API provider type."""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"


@dataclass
class APIConfig:
    """API configuration for model connections."""
    base_url: str = "http://127.0.0.1:8000/v1"
    api_key: str = "local-token"
    model: str = "Qwen/Qwen3-Coder-30B-A3B-Instruct"
    temperature: float = 0.1
    max_tokens: Optional[int] = None
    provider: APIProvider = APIProvider.OPENAI_COMPATIBLE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "provider": self.provider.value,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> APIConfig:
        # Support camelCase and snake_case
        base_url = data.get("baseUrl") or data.get("base_url") or "http://127.0.0.1:8000/v1"
        api_key = data.get("apiKey") or data.get("api_key") or "local-token"
        model = data.get("model") or data.get("model_name") or "Qwen/Qwen3-Coder-30B-A3B-Instruct"

        provider_str = data.get("provider", "openai_compatible")
        try:
            provider = APIProvider(provider_str)
        except ValueError:
            provider = APIProvider.OPENAI_COMPATIBLE

        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=data.get("temperature", 0.1),
            max_tokens=data.get("max_tokens") or data.get("maxTokens"),
            provider=provider,
        )


class APIConfigRuntime(RuntimeBase):
    """API configuration runtime.

    Discovers API configuration from:
    - .claude/settings.json (model section)
    - .claw-config.json (api section)
    - .env file in cwd
    - Environment variables (fallback)

    Environment variables take precedence over config files.

    Supported environment variables:
    - OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL (OpenAI-compatible)
    - ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY, ANTHROPIC_MODEL (Anthropic-native)
    - ANTHROPIC_AUTH_TOKEN (alternative to ANTHROPIC_API_KEY for MiniMax)
    """

    def __init__(self, cwd: str):
        self.cwd = cwd
        self._provider_set_from_file = False
        self.config = self._load_config()

    def _load_config(self) -> APIConfig:
        """Load API configuration with correct precedence:
        1. JSON config files (lowest priority)
        2. .env file in cwd
        3. OS environment variables (highest priority)
        """
        # Start with defaults
        config = APIConfig()

        # Step 1: Load from JSON config files (.claude/settings.json, etc.)
        file_config = self._discover_from_file()
        if file_config:
            config = file_config

        # Step 2: Load from .env file (overrides JSON config)
        self._load_from_env_file(config)

        # Step 3: OS environment variables take highest precedence
        # Only apply if the variable is explicitly set in the environment
        if os.environ.get("OPENAI_BASE_URL"):
            config.base_url = os.environ["OPENAI_BASE_URL"]
            # Only switch provider if we were already using openai-compatible
            # or if no provider was explicitly set via .env file
            if config.provider == APIProvider.OPENAI_COMPATIBLE:
                pass  # keep existing provider
            elif not self._provider_set_from_file:
                config.provider = APIProvider.OPENAI_COMPATIBLE
        if os.environ.get("OPENAI_API_KEY"):
            config.api_key = os.environ["OPENAI_API_KEY"]
        if os.environ.get("OPENAI_MODEL"):
            config.model = os.environ["OPENAI_MODEL"]

        # Check for Anthropic-specific env vars (separate from openai vars)
        anthropic_base_url = os.environ.get("ANTHROPIC_BASE_URL")
        anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        anthropic_model = os.environ.get("ANTHROPIC_MODEL")

        if anthropic_base_url:
            config.base_url = anthropic_base_url
            config.provider = APIProvider.ANTHROPIC
        if anthropic_api_key:
            config.api_key = anthropic_api_key
            config.provider = APIProvider.ANTHROPIC
        if anthropic_model:
            config.model = anthropic_model
            config.provider = APIProvider.ANTHROPIC

        return config

    def _load_from_env_file(self, config: APIConfig) -> None:
        """Load configuration from .env file in cwd."""
        env_path = os.path.join(self.cwd, ".env")
        if not os.path.exists(env_path):
            return

        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue

                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip()

                    # Remove quotes if present
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]

                    # Map to config fields
                    if key == "ANTHROPIC_BASE_URL":
                        config.base_url = value
                        config.provider = APIProvider.ANTHROPIC
                        self._provider_set_from_file = True
                    elif key == "ANTHROPIC_AUTH_TOKEN":
                        config.api_key = value
                        config.provider = APIProvider.ANTHROPIC
                        self._provider_set_from_file = True
                    elif key == "ANTHROPIC_API_KEY":
                        config.api_key = value
                        config.provider = APIProvider.ANTHROPIC
                        self._provider_set_from_file = True
                    elif key == "ANTHROPIC_MODEL":
                        config.model = value
                    elif key == "OPENAI_BASE_URL":
                        config.base_url = value
                        config.provider = APIProvider.OPENAI_COMPATIBLE
                        self._provider_set_from_file = True
                    elif key == "OPENAI_API_KEY":
                        config.api_key = value
                    elif key == "OPENAI_MODEL":
                        config.model = value
        except (OSError, IOError):
            pass

    def _discover_from_file(self) -> Optional[APIConfig]:
        """Discover API config from file."""
        import json

        # Check .claude/settings.json
        settings_path = os.path.join(self.cwd, ".claude", "settings.json")
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # Look for model section
                model_data = data.get("model", {})
                if model_data:
                    return APIConfig.from_dict(model_data)

                # Or look for api section
                api_data = data.get("api", {})
                if api_data:
                    return APIConfig.from_dict(api_data)
            except (json.JSONDecodeError, OSError):
                pass

        # Check .claude/settings.local.json
        local_path = os.path.join(self.cwd, ".claude", "settings.local.json")
        if os.path.exists(local_path):
            try:
                with open(local_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                model_data = data.get("model", {})
                if model_data:
                    return APIConfig.from_dict(model_data)
            except (json.JSONDecodeError, OSError):
                pass

        # Check .claw-config.json
        claw_path = os.path.join(self.cwd, ".claw-config.json")
        if os.path.exists(claw_path):
            try:
                with open(claw_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                api_data = data.get("api", {})
                if api_data:
                    return APIConfig.from_dict(api_data)

                model_data = data.get("model", {})
                if model_data:
                    return APIConfig.from_dict(model_data)
            except (json.JSONDecodeError, OSError):
                pass

        return None

    def get_state(self) -> Dict[str, Any]:
        """Get current state."""
        return {
            "provider": self.config.provider.value,
            "base_url": self.config.base_url,
            "model": self.config.model,
            "temperature": self.config.temperature,
            "has_api_key": bool(self.config.api_key and self.config.api_key != "local-token"),
        }

    def get_config(self) -> APIConfig:
        """Get the full API config."""
        return self.config

    def render_summary(self) -> str:
        """Render summary for context injection."""
        return f"[API Config] {self.config.provider.value}: {self.config.model} @ {self.config.base_url}"

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        return f"Using model: {self.config.model} via {self.config.provider.value}"


# Convenience function for getting API config
def get_api_config(cwd: str = ".") -> APIConfig:
    """Get API configuration for the given working directory."""
    runtime = APIConfigRuntime(cwd=cwd)
    return runtime.get_config()