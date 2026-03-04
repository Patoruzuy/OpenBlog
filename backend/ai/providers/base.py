"""Abstract base class for AI review providers.

All providers must implement :meth:`run_review` and return a dict conforming
to the schema documented below.

Return schema
-------------
    {
        "summary_md": str,         # Short Markdown summary (≤ 500 chars recommended)
        "findings_json": [         # List of finding objects
            {
                "severity":       "info" | "warn" | "high",
                "category":       "clarity" | "architecture" | "security" | "general",
                "message":        str,
                "suggested_fix":  str | None  # optional
            },
            ...
        ],
        "metrics_json": {          # Provider telemetry (safe to log)
            "provider":       str,
            "model":          str,
            "latency_ms":     int | None,
            "prompt_tokens":  int | None,
            "output_tokens":  int | None,
        }
    }

Analytics explanation schema
-----------------------------
:meth:`run_explanation` returns a plain Markdown string (≤ 3 000 chars).
The content must include:
  - A one-sentence executive summary.
  - Three bullet "Observations" grounded only in the supplied metrics.
  - Three bullet "Suggested next steps" that are actionable and specific.
  - No fabricated statistics; never claim certainty about future behaviour.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class AIReviewProvider(ABC):
    """Interface every AI provider must implement."""

    #: Human-readable name used for logging and stored in ``ai_review_results.provider``.
    name: str = "base"

    @abstractmethod
    def run_review(
        self,
        input_text: str,
        review_type: str,
        context: dict,
    ) -> dict:
        """Execute the review and return a structured result dict.

        Parameters
        ----------
        input_text:
            The markdown or diff text to analyse.  May be the current post
            body, a proposed revision, or a unified diff depending on the
            request type.
        review_type:
            One of ``clarity``, ``security``, ``architecture``, ``full``.
        context:
            Supplementary metadata (e.g. ``{"post_title": ..., "workspace_name": ...}``).
            Providers may use this to tailor the prompt; they must not assume
            any particular key is present.

        Returns
        -------
        dict
            Must contain ``summary_md`` (str), ``findings_json`` (list),
            and ``metrics_json`` (dict).  See module docstring for the schema.
        """

    @abstractmethod
    def run_explanation(
        self,
        input_dict: dict,
        kind: str,
    ) -> str:
        """Generate a short analytics explanation and return it as Markdown.

        Parameters
        ----------
        input_dict:
            A validated, pre-truncated dict produced by
            ``prompt_analytics_explain_service.build_input()``.  Contains
            only the metrics and context relevant to *kind*.
        kind:
            One of ``trend``, ``fork_rationale``, ``version_diff``.

        Returns
        -------
        str
            Markdown text (≤ 3 000 chars after provider returns).  Must follow
            the format documented in the module docstring: summary sentence,
            three Observations bullets, three Suggested next steps bullets.
        """
