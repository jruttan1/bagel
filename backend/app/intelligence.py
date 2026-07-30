"""Evidence-first investment analysis and final message writing."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app import crud
from app.config import Settings
from app.market import MarketDataClient, MarketDataUnavailable, research_candidates
from app.models import ConversationMessage, InvestmentThesis, PortfolioSnapshot, User
from app.prompts import (
    PROFILE_INSTRUCTIONS,
    QUESTION_INSTRUCTIONS,
    RESEARCH_INSTRUCTIONS,
    RESEARCH_PLANNER_INSTRUCTIONS,
    WRITER_INSTRUCTIONS,
)
from app.schemas import MessageDraft, ResearchAnalysis, ResearchPlan

SchemaModel = TypeVar("SchemaModel", bound=BaseModel)
FORBIDDEN_LABEL = re.compile(r"(?im)^\s*(ticker|sentiment|thesis status|action)\s*:")
VISIBLE_FORMATTING = re.compile(r"(?:\*\*|__|^#{1,6}\s|\[[^]]+\]\(https?://)", re.MULTILINE)


class IntelligenceUnavailable(RuntimeError):
    pass


class IntelligenceService:
    def __init__(
        self,
        settings: Settings,
        market: MarketDataClient | None = None,
        client: AsyncOpenAI | None = None,
    ):
        self.settings = settings
        self.market = market
        self.client = client or (
            AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
        )

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
    ) -> MessageDraft:
        history = [
            {"direction": message.direction.value, "content": message.content}
            for message in messages[-10:]
        ]
        plan, _ = await self._structured(
            ResearchPlan,
            "research_plan",
            RESEARCH_PLANNER_INSTRUCTIONS,
            {
                "current_message": incoming_text,
                "recent_conversation": history,
                "held_tickers": _held_tickers(snapshot),
            },
            effort="low",
        )
        analysis = None
        signals = []
        if plan.needs_current_research and snapshot is not None and self.market is not None:
            try:
                signals = await self.market.signals(snapshot)
                selected = [signal for signal in signals if signal.ticker in set(plan.tickers)]
                analysis = await self._research(
                    user, selected or research_candidates(signals), theses, []
                )
            except MarketDataUnavailable:
                analysis = ResearchAnalysis(
                    material_change=False,
                    avoid_claims=["Current market prices are unavailable; do not infer a move or cause."],
                )
        payload = {
            "message_kind": "direct_reply",
            "current_message": incoming_text,
            "recent_conversation": history,
            "internal_user_context": _user_context(user),
            "portfolio": _snapshot_context(snapshot),
            "saved_theses": [_thesis_context(thesis) for thesis in theses if thesis.is_active],
            "market_signals": [signal.model_dump(mode="json") for signal in signals[:5]],
            "approved_analysis": _writer_analysis(analysis),
        }
        return await self._write(
            payload,
            fallback="I don’t have enough reliable information to answer that yet.",
            evidence=analysis.model_dump(mode="json") if analysis else {},
        )

    async def morning_brief(
        self,
        user: User,
        snapshot: PortfolioSnapshot,
        prior_snapshot: PortfolioSnapshot | None,
        theses: list[InvestmentThesis],
        upcoming_events: list[dict] | None = None,
    ) -> MessageDraft:
        try:
            signals = await self.market.signals(snapshot) if self.market is not None else []
        except MarketDataUnavailable as exc:
            raise IntelligenceUnavailable("Market data is unavailable for this brief") from exc
        candidates = research_candidates(signals)
        analysis = await self._research(user, candidates, theses, upcoming_events or [])
        fallback = _brief_fallback(candidates)
        payload = {
            "message_kind": "morning_brief",
            "internal_user_context": _user_context(user),
            "portfolio": _snapshot_context(snapshot),
            "portfolio_change": _portfolio_change(snapshot, prior_snapshot),
            "saved_theses": [_thesis_context(thesis) for thesis in theses if thesis.is_active],
            "ranked_market_signals": [signal.model_dump(mode="json") for signal in candidates],
            "upcoming_events": upcoming_events or [],
            "approved_analysis": _writer_analysis(analysis),
        }
        return await self._write(
            payload,
            fallback=fallback,
            evidence=analysis.model_dump(mode="json"),
        )

    async def _research(
        self,
        user: User,
        signals,
        theses: list[InvestmentThesis],
        events: list[dict],
    ) -> ResearchAnalysis:
        if not signals and not events:
            return ResearchAnalysis(material_change=False)
        cache_key = _research_key(user, signals, theses, events)
        cached = await crud.cached_research(cache_key)
        if cached:
            return ResearchAnalysis.model_validate(cached)
        try:
            analysis, response = await self._structured(
                ResearchAnalysis,
                "market_research",
                RESEARCH_INSTRUCTIONS,
                {
                    "ranked_signals": [signal.model_dump(mode="json") for signal in signals],
                    "saved_theses": [
                        _thesis_context(thesis) for thesis in theses if thesis.is_active
                    ],
                    "upcoming_events": events,
                    "research_cutoff": max(
                        (signal.observed_at for signal in signals), default=None
                    ),
                },
                effort="medium",
                tools=[{"type": "web_search"}],
            )
            analysis.source_urls = _citation_urls(response)
            await crud.cache_research(cache_key, analysis.model_dump(mode="json"))
            return analysis
        except IntelligenceUnavailable:
            return ResearchAnalysis(
                material_change=bool(signals),
                facts=[
                    {
                        "text": _signal_fact(signal),
                        "kind": "price",
                        "tickers": [signal.ticker],
                        "confidence": "high",
                    }
                    for signal in signals[:2]
                ],
                avoid_claims=["Do not assign a cause to the move because research was unavailable."],
            )

    async def _write(self, payload: dict, *, fallback: str, evidence: dict) -> MessageDraft:
        for _ in range(2):
            try:
                draft, _ = await self._structured(
                    MessageDraft,
                    "message_draft",
                    WRITER_INSTRUCTIONS,
                    payload,
                    effort="low",
                )
                draft = _validate_draft(
                    draft,
                    self.settings.max_message_chars,
                    morning=payload.get("message_kind") == "morning_brief",
                )
                draft._evidence = evidence
                return draft
            except IntelligenceUnavailable:
                continue
        draft = MessageDraft(text=fallback)
        draft._evidence = evidence
        return draft

    async def _structured(
        self,
        output: type[SchemaModel],
        name: str,
        instructions: str,
        payload: dict,
        *,
        effort: str,
        tools: list[dict] | None = None,
    ) -> tuple[SchemaModel, Any]:
        request: dict[str, Any] = {
            "model": self.settings.openai_model,
            "instructions": instructions,
            "input": json.dumps(payload, default=str),
            "reasoning": {"effort": effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": name,
                    "strict": True,
                    "schema": _strict_schema(output),
                },
                "verbosity": "low",
            },
        }
        if tools:
            request["tools"] = tools
        try:
            response = await self._require_client().responses.create(**request)
            return output.model_validate_json(response.output_text), response
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise IntelligenceUnavailable(f"{name} returned an invalid result") from exc
        except Exception as exc:
            raise IntelligenceUnavailable(f"{name} request failed") from exc

    async def distill_profile(
        self, answers: list[dict[str, str]], snapshot: PortfolioSnapshot | None
    ) -> dict[str, Any]:
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

        profile, _ = await self._structured(
            InvestorProfile,
            "investor_context",
            PROFILE_INSTRUCTIONS,
            {"answers": answers, "portfolio_characteristics": _snapshot_context(snapshot)},
            effort="low",
        )
        return profile.model_dump()

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
        raise IntelligenceUnavailable("Writer returned links or visible formatting")
    if FORBIDDEN_LABEL.search(text):
        raise IntelligenceUnavailable("Writer returned a forbidden label")
    if morning and len(text.split()) > 220:
        raise IntelligenceUnavailable("Writer returned an overly long morning brief")
    emphasis = (draft.emphasis_phrase or "").strip() or None
    if emphasis and (emphasis not in text or "\n" in emphasis):
        emphasis = None
    return MessageDraft(text=text, emphasis_phrase=emphasis)


def _writer_analysis(analysis: ResearchAnalysis | None) -> dict | None:
    if analysis is None:
        return None
    value = analysis.model_dump(mode="json")
    value.pop("source_urls", None)
    return value


def _research_key(user: User, signals, theses: list[InvestmentThesis], events: list[dict]) -> str:
    value = {
        "user": str(user.id),
        "signals": [
            {
                "ticker": signal.ticker,
                "regular": signal.regular_change_percent,
                "extended": signal.extended_change_percent,
                "observed": signal.observed_at.isoformat(),
            }
            for signal in signals
        ],
        "theses": [
            {"ticker": thesis.ticker, "updated": thesis.updated_at.isoformat()}
            for thesis in theses
            if thesis.is_active
        ],
        "events": events,
    }
    return hashlib.sha256(json.dumps(value, default=str, sort_keys=True).encode()).hexdigest()


def _citation_urls(response: Any) -> list[str]:
    try:
        value = response.model_dump(mode="json")
    except (AttributeError, TypeError):
        return []
    urls: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if item.get("type") == "url_citation" and isinstance(item.get("url"), str):
                urls.append(item["url"])
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return list(dict.fromkeys(urls))


def _held_tickers(snapshot: PortfolioSnapshot | None) -> list[str]:
    return sorted({holding.ticker.upper() for holding in snapshot.holdings}) if snapshot else []


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
            changes,
            key=lambda row: abs(row["current_value"] - row["prior_value"]),
            reverse=True,
        )[:15],
    }


def _signal_fact(signal) -> str:
    move = (
        signal.extended_change_percent
        if signal.extended_change_percent is not None and signal.session != "regular"
        else signal.regular_change_percent
    )
    return f"{signal.ticker} moved {move:+.1f}% in the {signal.session} session."


def _brief_fallback(signals) -> str:
    if not signals:
        return "Nothing in your portfolio moved enough to change the picture this morning."
    lead = signals[0]
    move = lead.extended_change_percent or lead.regular_change_percent or 0
    return (
        f"{lead.ticker} is the main move in your portfolio at {move:+.1f}%. "
        "I couldn’t verify a reliable company-specific reason yet, so I wouldn’t read more into it than that."
    )


def _clean_text(value: str, max_chars: int) -> str:
    text = (value or "").strip()
    if not text:
        raise IntelligenceUnavailable("The model returned an empty message")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"
