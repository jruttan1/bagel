"""Stateful LangGraph investment agent shared by messages and scheduled briefs."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app import crud
from app.config import Settings
from app.intelligence import IntelligenceUnavailable, _validate_draft
from app.market import MarketDataClient, MarketDataUnavailable, research_candidates
from app.schemas import MessageDraft

AGENT_INSTRUCTIONS = """
You are bagel, a stateful personal investment agent that replies through iMessage.

Use tools to get account facts instead of guessing. For a scheduled morning brief, always inspect the
portfolio and market signals first, then research only movers or events that could matter. For an inbound
message, inspect the portfolio when personal context changes the answer; use current market tools and web
search only when the question depends on recent facts. Prefer company releases, filings, earnings material,
regulators, and exchanges over market commentary. Saved theses are hypotheses, not facts.

Lead with the one thing the person most needs to understand. Explain portfolio impact before general market
news. Mention only holdings that materially affected the portfolio or thesis. Separate price movement, market
narrative, and business evidence. Say when nothing meaningful changed. Personalize quietly.

Write one natural text with no source links, citations, predefined headings, labels, generic disclaimer, or
forced conclusion. Do not repeat numbers already explained. Avoid commands such as buy, sell, hold, panic, or
ignore. Normal briefs are roughly 100 to 180 words; direct replies should be only as long as useful. You may
choose one short phrase for native emphasis. The text itself contains no Markdown.

Use the brief-time tool only when the person clearly asks to change delivery time. Never send messages or
place trades; delivery and scheduling are controlled by the application.
""".strip()


class AgentUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentContext:
    user_id: str
    trigger: Literal["inbound", "cron"]


class BagelAgent:
    def __init__(self, settings: Settings, market: MarketDataClient, checkpointer):
        self.settings = settings
        self.market = market
        model = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            use_responses_api=True,
            reasoning_effort="low",
            verbosity="low",
            timeout=45,
            max_retries=2,
            store=False,
        )
        self.graph = create_agent(
            model=model,
            tools=[*_build_tools(market), {"type": "web_search"}],
            system_prompt=AGENT_INSTRUCTIONS,
            response_format=ToolStrategy(MessageDraft),
            context_schema=AgentContext,
            checkpointer=checkpointer,
            name="bagel",
        )

    async def reply(self, user_id: UUID, text: str) -> MessageDraft:
        if await crud.user_by_id(user_id) is None:
            raise AgentUnavailable("User not found")
        return await self._run(user_id, "inbound", text)

    async def morning_brief(self, user_id: UUID) -> MessageDraft:
        return await self._run(
            user_id,
            "cron",
            "Prepare the scheduled morning portfolio brief using information available right now.",
        )

    async def _run(
        self,
        user_id: UUID,
        trigger: Literal["inbound", "cron"],
        prompt: str,
    ) -> MessageDraft:
        context = AgentContext(user_id=str(user_id), trigger=trigger)
        config = {"configurable": {"thread_id": str(user_id)}, "recursion_limit": 24}
        current_prompt = prompt
        last_error: Exception | None = None
        for _ in range(2):
            try:
                result = await self.graph.ainvoke(
                    {"messages": [{"role": "user", "content": current_prompt}]},
                    config=config,
                    context=context,
                )
                draft = result.get("structured_response")
                if not isinstance(draft, MessageDraft):
                    raise AgentUnavailable("Agent did not return a message draft")
                draft = _validate_draft(
                    draft,
                    self.settings.max_message_chars,
                    morning=trigger == "cron",
                )
                evidence = _evidence(result.get("messages", []))
                if trigger == "cron" and "get_market_signals" not in evidence["tools"]:
                    raise AgentUnavailable("Morning brief skipped market signals")
                if trigger == "cron" and "market_data_unavailable" in evidence["errors"]:
                    raise AgentUnavailable("Market data is unavailable for this brief")
                draft._evidence = evidence
                return draft
            except (AgentUnavailable, IntelligenceUnavailable, ValueError, TypeError) as exc:
                last_error = exc
                current_prompt = (
                    "Rewrite the answer as one valid plain-text iMessage. Remove links, visible Markdown, "
                    "labels, unsupported claims, and unnecessary structure."
                )
            except Exception as exc:
                raise AgentUnavailable("Agent run failed") from exc
        raise AgentUnavailable("Agent could not produce a valid message") from last_error


@asynccontextmanager
async def agent_runtime(settings: Settings, market: MarketDataClient):
    if settings.database_url.startswith("postgresql"):
        uri = _checkpoint_database_uri(settings.database_url)
        async with AsyncPostgresSaver.from_conn_string(uri) as checkpointer:
            await checkpointer.setup()
            yield BagelAgent(settings, market, checkpointer)
        return
    async with AsyncSqliteSaver.from_conn_string(settings.agent_checkpoint_path) as checkpointer:
        await checkpointer.setup()
        yield BagelAgent(settings, market, checkpointer)


def _checkpoint_database_uri(database_url: str) -> str:
    """Translate the application's asyncpg URL into a psycopg-compatible URL."""
    parsed = urlsplit(database_url.replace("postgresql+asyncpg://", "postgresql://", 1))
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "ssl" in query and "sslmode" not in query:
        query["sslmode"] = query.pop("ssl")
    return urlunsplit(parsed._replace(query=urlencode(query)))


def _build_tools(market: MarketDataClient) -> list:
    @tool
    async def get_portfolio(runtime: ToolRuntime[AgentContext]) -> dict:
        """Get the current user's portfolio, profile, theses, and recent conversation."""
        user_id = UUID(runtime.context.user_id)
        user = await crud.user_by_id(user_id)
        snapshot = await crud.latest_snapshot(user_id)
        if user is None:
            return {"error": "user_not_found"}
        history = await crud.recent_messages(user_id, limit=10)
        return {
            "profile": user.profile_data,
            "profile_summary": user.profile_summary,
            "preferences": user.preferences,
            "notification_settings": user.notification_settings,
            "portfolio": _snapshot(snapshot),
            "theses": [_thesis(thesis) for thesis in user.theses if thesis.is_active],
            "recent_conversation": [
                {"direction": message.direction.value, "content": message.content}
                for message in history
            ],
        }

    @tool
    async def get_market_signals(runtime: ToolRuntime[AgentContext]) -> dict:
        """Get fresh, deterministic regular and extended-hours moves for held securities."""
        snapshot = await crud.latest_snapshot(UUID(runtime.context.user_id))
        if snapshot is None:
            return {"error": "portfolio_unavailable"}
        try:
            signals = await market.signals(snapshot)
        except MarketDataUnavailable:
            return {"error": "market_data_unavailable"}
        candidates = {signal.ticker for signal in research_candidates(signals)}
        return {
            "signals": [
                {**signal.model_dump(mode="json"), "research_candidate": signal.ticker in candidates}
                for signal in signals[:10]
            ]
        }

    @tool
    async def get_company_events(days: int, runtime: ToolRuntime[AgentContext]) -> dict:
        """Get stored upcoming and recent events for held companies, up to 30 days."""
        days = min(max(days, 1), 30)
        snapshot = await crud.latest_snapshot(UUID(runtime.context.user_id))
        tickers = {holding.ticker.upper() for holding in snapshot.holdings} if snapshot else set()
        rows = await crud.market_events(tickers, datetime.now(UTC) - timedelta(days=1), days)
        return {"events": rows}

    @tool
    async def get_earnings(days: int, runtime: ToolRuntime[AgentContext]) -> dict:
        """Get upcoming earnings dates for held companies, up to 30 days."""
        days = min(max(days, 1), 30)
        snapshot = await crud.latest_snapshot(UUID(runtime.context.user_id))
        if snapshot is None:
            return {"events": []}
        tickers = {holding.ticker.upper() for holding in snapshot.holdings}
        start = datetime.now(UTC).date()
        rows = await market.earnings_calendar(start, start + timedelta(days=days))
        return {
            "events": [
                row for row in rows if str(row.get("symbol") or "").upper() in tickers
            ]
        }

    @tool
    async def set_morning_brief_time(time: str, runtime: ToolRuntime[AgentContext]) -> dict:
        """Set the user's morning brief time as a local 24-hour HH:MM value."""
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time):
            return {"error": "invalid_time", "expected": "HH:MM"}
        user_id = UUID(runtime.context.user_id)
        await crud.set_brief_time(user_id, time)
        user = await crud.user_by_id(user_id)
        return {"brief_time": time, "timezone": user.timezone if user else "UTC"}

    return [
        get_portfolio,
        get_market_signals,
        get_company_events,
        get_earnings,
        set_morning_brief_time,
    ]


def _snapshot(snapshot) -> dict | None:
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


def _thesis(thesis) -> dict:
    return {
        "ticker": thesis.ticker,
        "why_owned": thesis.why_owned,
        "bull_case": thesis.bull_case,
        "bear_case": thesis.bear_case,
        "important_metrics": thesis.important_metrics,
        "invalidation_conditions": thesis.invalidation_conditions,
        "sophistication": thesis.sophistication,
    }


def _evidence(messages: list) -> dict:
    tools: list[str] = []
    errors: list[str] = []
    urls: list[str] = []
    for message in messages:
        if isinstance(message, AIMessage):
            tools.extend(call.get("name", "") for call in message.tool_calls)
            _collect_urls(message.content_blocks, urls)
        elif isinstance(message, ToolMessage):
            if message.name:
                tools.append(message.name)
            if isinstance(message.content, str):
                errors.extend(
                    value
                    for value in ("market_data_unavailable", "portfolio_unavailable")
                    if value in message.content
                )
    return {
        "tools": list(dict.fromkeys(filter(None, tools))),
        "errors": list(dict.fromkeys(errors)),
        "source_urls": list(dict.fromkeys(urls)),
    }


def _collect_urls(value: Any, urls: list[str]) -> None:
    if isinstance(value, dict):
        url = value.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            urls.append(url)
        for child in value.values():
            _collect_urls(child, urls)
    elif isinstance(value, list):
        for child in value:
            _collect_urls(child, urls)
