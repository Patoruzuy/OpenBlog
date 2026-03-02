"""AI review provider package — public interface.

Import :func:`get_provider` to get the configured provider instance.
"""

from __future__ import annotations

from backend.ai.providers.base import AIReviewProvider
from backend.ai.providers.mock import MockAIProvider


def get_provider(config: dict) -> AIReviewProvider:
    """Return the configured :class:`AIReviewProvider` instance.

    Reads ``config["AI_PROVIDER"]`` (default ``"mock"``) and returns the
    matching implementation.  Raises :class:`ValueError` for unknown values.

    Parameters
    ----------
    config:
        Flask ``app.config`` dict (or any mapping with ``AI_PROVIDER`` key).
    """
    provider_name = (config.get("AI_PROVIDER") or "mock").lower()

    if provider_name == "mock":
        return MockAIProvider()

    if provider_name == "openai":
        from backend.ai.providers.openai_provider import OpenAIProvider  # noqa: PLC0415

        return OpenAIProvider(
            api_key=config.get("OPENAI_API_KEY") or "",
            model_name=config.get("AI_MODEL_NAME") or "gpt-4.1-mini",
            timeout=config.get("AI_TIMEOUT_SECONDS", 60),
        )

    if provider_name == "ollama":
        from backend.ai.providers.ollama_provider import OllamaProvider  # noqa: PLC0415

        return OllamaProvider(
            base_url=config.get("OLLAMA_BASE_URL") or "http://localhost:11434",
            model_name=config.get("AI_MODEL_NAME") or "llama3",
            timeout=config.get("AI_TIMEOUT_SECONDS", 60),
        )

    raise ValueError(
        f"Unknown AI_PROVIDER {provider_name!r}. "
        "Supported values: mock, openai, ollama"
    )


__all__ = ["AIReviewProvider", "get_provider"]
