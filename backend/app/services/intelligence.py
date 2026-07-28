import json
from decimal import Decimal
from typing import Any

from openai import AsyncOpenAI

from app.config import Settings
from app.models import ConversationMessage, Holding, InvestmentThesis, PortfolioSnapshot, User
from app.prompts import AGENT_INSTRUCTIONS, BRIEF_INSTRUCTIONS, PROFILE_INSTRUCTIONS, QUESTION_INSTRUCTIONS


class IntelligenceUnavailable(RuntimeError):
    pass


class IntelligenceService:
    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None):
        self.settings = settings
        self.client = client or (AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None)

    def _require_client(self) -> AsyncOpenAI:
        if self.client is None:
            raise IntelligenceUnavailable("OPENAI_API_KEY is not configured")
        return self.client

    async def reply(
        self,
        user: User,
        incoming_text: str,
        snapshot: PortfolioSnapshot | None,
        theses: list[InvestmentThesis],
        messages: list[ConversationMessage],
    ) -> str:
        context = {
            "internal_user_context": _user_context(user),
            "latest_portfolio": _snapshot_context(snapshot),
            "saved_theses": [_thesis_context(thesis) for thesis in theses if thesis.is_active],
            "recent_conversation": [
                {"direction": message.direction.value, "content": message.content} for message in messages[-10:]
            ],
            "current_user_message": incoming_text,
        }
        response = await self._require_client().responses.create(
            model=self.settings.openai_model,
            instructions=AGENT_INSTRUCTIONS,
            input=json.dumps(context, default=str),
            tools=[{"type": "web_search"}],
            reasoning={"effort": "medium"},
            text={"verbosity": "low"},
        )
        return _clean_message(response.output_text, self.settings.max_message_chars)

    async def morning_brief(
        self,
        user: User,
        snapshot: PortfolioSnapshot,
        prior_snapshot: PortfolioSnapshot | None,
        theses: list[InvestmentThesis],
        upcoming_events: list[dict] | None = None,
    ) -> str:
        payload = {
            "internal_user_context": _user_context(user),
            "current_portfolio": _snapshot_context(snapshot),
            "prior_portfolio": _snapshot_context(prior_snapshot),
            "portfolio_change": _portfolio_change(snapshot, prior_snapshot),
            "saved_theses": [_thesis_context(thesis) for thesis in theses if thesis.is_active],
            "upcoming_structured_events": upcoming_events or [],
        }
        response = await self._require_client().responses.create(
            model=self.settings.openai_model,
            instructions=BRIEF_INSTRUCTIONS,
            input=json.dumps(payload, default=str),
            tools=[{"type": "web_search"}],
            reasoning={"effort": "medium"},
            text={"verbosity": "low"},
        )
        return _clean_message(response.output_text, self.settings.max_message_chars)

    async def distill_profile(
        self, answers: list[dict[str, str]], snapshot: PortfolioSnapshot | None
    ) -> dict[str, Any]:
        payload = {"answers": answers, "portfolio_characteristics": _snapshot_context(snapshot)}
        response = await self._require_client().responses.create(
            model=self.settings.openai_model,
            instructions=PROFILE_INSTRUCTIONS,
            input=json.dumps(payload, default=str),
            reasoning={"effort": "low"},
            text={
                "format": {
                    "type": "json_schema",
                    "name": "investor_context",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "risk_capacity": {"type": "string"},
                            "time_horizon": {"type": "string"},
                            "liquidity_needs": {"type": "string"},
                            "investing_style": {"type": "string"},
                            "sophistication": {"type": "string"},
                            "concentration_tolerance": {"type": "string"},
                            "durable_interests": {"type": "array", "items": {"type": "string"}},
                            "uncertainty_notes": {"type": "array", "items": {"type": "string"}},
                            "summary": {"type": "string"},
                        },
                        "required": [
                            "risk_capacity",
                            "time_horizon",
                            "liquidity_needs",
                            "investing_style",
                            "sophistication",
                            "concentration_tolerance",
                            "durable_interests",
                            "uncertainty_notes",
                            "summary",
                        ],
                        "additionalProperties": False,
                    },
                },
                "verbosity": "low",
            },
        )
        try:
            value = json.loads(response.output_text)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError as exc:
            raise IntelligenceUnavailable("Profile extraction returned invalid JSON") from exc

    async def onboarding_question(
        self, category: str, snapshot: PortfolioSnapshot | None
    ) -> str:
        payload = {"category": category, "portfolio_characteristics": _snapshot_context(snapshot)}
        response = await self._require_client().responses.create(
            model=self.settings.openai_model,
            instructions=QUESTION_INSTRUCTIONS,
            input=json.dumps(payload, default=str),
            reasoning={"effort": "low"},
            text={"verbosity": "low"},
        )
        question = _clean_message(response.output_text, 240)
        return question if question.endswith("?") else f"{question.rstrip('.')}?"


def _user_context(user: User) -> dict:
    return {
        "profile": user.profile_data,
        "profile_summary": user.profile_summary,
        "preferences": user.preferences,
    }


def _snapshot_context(snapshot: PortfolioSnapshot | None) -> dict | None:
    if snapshot is None:
        return None
    holdings = sorted(snapshot.holdings, key=lambda item: item.current_value, reverse=True)
    return {
        "captured_at": snapshot.captured_at.isoformat(),
        "total_value": float(snapshot.total_value),
        "cash": float(snapshot.cash),
        "currency": snapshot.currency,
        "holdings": [
            {
                "ticker": holding.ticker,
                "name": holding.name,
                "value": float(holding.current_value),
                "weight": float(holding.portfolio_weight),
                "shares": float(holding.shares),
                "average_cost": float(holding.average_cost) if holding.average_cost is not None else None,
            }
            for holding in holdings[:30]
        ],
    }


def _thesis_context(thesis: InvestmentThesis) -> dict:
    return {
        "ticker": thesis.ticker,
        "why_owned": thesis.why_owned,
        "bull_case": thesis.bull_case,
        "bear_case": thesis.bear_case,
        "important_metrics": thesis.important_metrics,
        "invalidation_conditions": thesis.invalidation_conditions,
        "sophistication": thesis.sophistication,
    }


def _portfolio_change(current: PortfolioSnapshot, prior: PortfolioSnapshot | None) -> dict | None:
    if prior is None:
        return None
    prior_by_ticker = {holding.ticker: holding for holding in prior.holdings}
    changes = []
    for holding in current.holdings:
        old = prior_by_ticker.get(holding.ticker)
        changes.append(
            {
                "ticker": holding.ticker,
                "current_weight": float(holding.portfolio_weight),
                "prior_weight": float(old.portfolio_weight) if old else 0,
                "current_value": float(holding.current_value),
                "prior_value": float(old.current_value) if old else 0,
            }
        )
    return {
        "total_value_change": float(current.total_value - prior.total_value),
        "holding_changes": sorted(
            changes, key=lambda row: abs(row["current_value"] - row["prior_value"]), reverse=True
        )[:15],
    }


def _clean_message(value: str, max_chars: int) -> str:
    text = (value or "").strip()
    if not text:
        raise IntelligenceUnavailable("The model returned an empty message")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"

