"""Ollama provider stub — wired up only when AI_PROVIDER=ollama.

This module is intentionally *not* imported by default.  It is loaded
lazily by :func:`backend.ai.providers.get_provider` only when configured.
"""

from __future__ import annotations

from backend.ai.providers.base import AIReviewProvider


class OllamaProvider(AIReviewProvider):
    """Stub for Ollama-backed reviews.  Not implemented in v1."""

    name: str = "ollama"

    def __init__(self, base_url: str, model_name: str, timeout: int = 60) -> None:
        self._base_url = base_url
        self._model_name = model_name
        self._timeout = timeout

    def run_review(
        self,
        input_text: str,
        review_type: str,
        context: dict,
    ) -> dict:
        raise NotImplementedError(
            "Ollama provider is not implemented in v1. "
            "Set AI_PROVIDER=mock or implement this method."
        )
