"""Small model helpers used only during onboarding."""

import json
import re
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.models import PortfolioSnapshot
from app.prompts import PROFILE_INSTRUCTIONS, QUESTION_INSTRUCTIONS
from app.schemas import MessageDraft

FORBIDDEN_LABEL = re.compile(r"(?im)^\s*(ticker|sentiment|thesis status|action)\s*:")
VISIBLE_FORMATTING = re.compile(r"(?:\*\*|__|^#{1,6}\s|\[[^]]+\]\(https?://)", re.MULTILINE)


class IntelligenceUnavailable(RuntimeError):
    pass


class InvestorProfile(BaseModel):
    risk_capacity: str
    time_horizon: str
    liquidity_needs: str
    investing_style: str
    sophistication: str
    concentration_tolerance: str
    durable_interests: list[str]
    uncertainty_notes: list[str]
    summary: str


class OnboardingIntelligence:
    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None):
        self.settings = settings
        self.client = client or (
            AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
        )

    def _require_client(self) -> AsyncOpenAI:
        if self.client is None:
            raise IntelligenceUnavailable("OPENAI_API_KEY is not configured")
        return self.client

    async def distill_profile(
        self, answers: list[dict[str, str]], snapshot: PortfolioSnapshot | None
    ) -> dict[str, Any]:
        response = await self._require_client().responses.create(
            model=self.settings.openai_model,
            instructions=PROFILE_INSTRUCTIONS,
            input=json.dumps(
                {"answers": answers, "portfolio_characteristics": _snapshot_context(snapshot)},
                default=str,
            ),
            reasoning={"effort": "low"},
            text={
                "format": {
                    "type": "json_schema",
                    "name": "investor_context",
                    "strict": True,
                    "schema": _strict_schema(InvestorProfile),
                },
                "verbosity": "low",
            },
        )
        try:
            return InvestorProfile.model_validate_json(response.output_text).model_dump()
        except (ValidationError, ValueError, TypeError) as exc:
            raise IntelligenceUnavailable("Profile extraction returned invalid data") from exc

    async def onboarding_question(self, category: str, snapshot: PortfolioSnapshot | None) -> str:
        response = await self._require_client().responses.create(
            model=self.settings.openai_model,
            instructions=QUESTION_INSTRUCTIONS,
            input=json.dumps(
                {"category": category, "portfolio_characteristics": _snapshot_context(snapshot)},
                default=str,
            ),
            reasoning={"effort": "low"},
            text={"verbosity": "low"},
        )
        question = _clean_text(response.output_text, 240)
        return question if question.endswith("?") else f"{question.rstrip('.')}?"


def _strict_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object" and "properties" in value:
                value["additionalProperties"] = False
                value["required"] = list(value["properties"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    return schema


def _validate_draft(draft: MessageDraft, max_chars: int, *, morning: bool = False) -> MessageDraft:
    text = _clean_text(draft.text, max_chars)
    if "http://" in text or "https://" in text or VISIBLE_FORMATTING.search(text):
        raise IntelligenceUnavailable("Agent returned links or visible formatting")
    if FORBIDDEN_LABEL.search(text):
        raise IntelligenceUnavailable("Agent returned a forbidden label")
    if morning and len(text.split()) > 220:
        raise IntelligenceUnavailable("Agent returned an overly long morning brief")
    emphasis = (draft.emphasis_phrase or "").strip() or None
    if emphasis and (emphasis not in text or "\n" in emphasis):
        emphasis = None
    return MessageDraft(text=text, emphasis_phrase=emphasis)


def _snapshot_context(snapshot: PortfolioSnapshot | None) -> dict | None:
    if snapshot is None:
        return None
    holdings = sorted(snapshot.holdings, key=lambda item: item.current_value, reverse=True)
    return {
        "total_value": float(snapshot.total_value),
        "cash": float(snapshot.cash),
        "currency": snapshot.currency,
        "holdings": [
            {
                "ticker": holding.ticker,
                "weight": float(holding.portfolio_weight),
                "value": float(holding.current_value),
            }
            for holding in holdings[:30]
        ],
    }


def _clean_text(value: str, max_chars: int) -> str:
    text = (value or "").strip()
    if not text:
        raise IntelligenceUnavailable("The model returned an empty message")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"
