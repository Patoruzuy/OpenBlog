"""OpenAI provider stub — wired up only when AI_PROVIDER=openai.

This module is intentionally *not* imported by default.  It is loaded
lazily by :func:`backend.ai.providers.get_provider` only when configured.

To fully implement: install ``openai`` package and replace the
``NotImplementedError`` body with actual API calls.
"""

from __future__ import annotations

from backend.ai.providers.base import AIReviewProvider


class OpenAIProvider(AIReviewProvider):
    """Stub for OpenAI-backed reviews.  Not implemented in v1."""

    name: str = "openai"

    def __init__(self, api_key: str, model_name: str, timeout: int = 60) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._timeout = timeout

    def run_review(
        self,
        input_text: str,
        review_type: str,
        context: dict,
    ) -> dict:
        raise NotImplementedError(
            "OpenAI provider is not implemented in v1. "
            "Set AI_PROVIDER=mock or implement this method."
        )
