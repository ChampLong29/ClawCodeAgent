"""Model configuration and pricing for CodeAgent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .agent_types import ModelPricing


# Default model configurations
DEFAULT_MODELS = {
    "Qwen/Qwen3-Coder-30B-A3B-Instruct": {
        "pricing": ModelPricing(input_token_price=0.00001, output_token_price=0.00003),
        "context_window": 128000,
        "supports_tools": True,
        "supports_streaming": True,
    },
    "gpt-4": {
        "pricing": ModelPricing(input_token_price=0.00003, output_token_price=0.00006),
        "context_window": 8192,
        "supports_tools": True,
        "supports_streaming": True,
    },
    "gpt-3.5-turbo": {
        "pricing": ModelPricing(input_token_price=0.000001, output_token_price=0.000002),
        "context_window": 16385,
        "supports_tools": True,
        "supports_streaming": True,
    },
    "claude-3-sonnet-20240229": {
        "pricing": ModelPricing(input_token_price=0.000003, output_token_price=0.000015),
        "context_window": 200000,
        "supports_tools": True,
        "supports_streaming": True,
    },
}


@dataclass
class ModelInfo:
    """Extended model information."""
    name: str
    pricing: ModelPricing
    context_window: int = 128000
    supports_tools: bool = True
    supports_streaming: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "pricing": self.pricing.to_dict(),
            "context_window": self.context_window,
            "supports_tools": self.supports_tools,
            "supports_streaming": self.supports_streaming,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ModelInfo:
        pricing = ModelPricing.from_dict(data.get("pricing", {}))
        return cls(
            name=data.get("name", ""),
            pricing=pricing,
            context_window=data.get("context_window", 128000),
            supports_tools=data.get("supports_tools", True),
            supports_streaming=data.get("supports_streaming", True),
            metadata=data.get("metadata", {}),
        )


def get_model_info(model_name: str) -> Optional[ModelInfo]:
    """Get model information by name."""
    if model_name in DEFAULT_MODELS:
        config = DEFAULT_MODELS[model_name]
        return ModelInfo(
            name=model_name,
            pricing=config["pricing"],
            context_window=config.get("context_window", 128000),
            supports_tools=config.get("supports_tools", True),
            supports_streaming=config.get("supports_streaming", True),
        )
    return None


def list_available_models() -> List[ModelInfo]:
    """List all available models."""
    return [
        ModelInfo(
            name=name,
            pricing=config["pricing"],
            context_window=config.get("context_window", 128000),
            supports_tools=config.get("supports_tools", True),
            supports_streaming=config.get("supports_streaming", True),
        )
        for name, config in DEFAULT_MODELS.items()
    ]


def calculate_run_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate the cost of a run."""
    info = get_model_info(model_name)
    if info:
        return info.pricing.calculate_cost(input_tokens, output_tokens)
    # Default pricing if model not found
    return (input_tokens * 0.00001) + (output_tokens * 0.00003)