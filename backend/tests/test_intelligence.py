import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import Settings
from app.intelligence import IntelligenceService, IntelligenceUnavailable, _validate_draft
from app.models import Holding, PortfolioSnapshot, User
from app.schemas import MarketSignal, MessageDraft


class FakeResponse:
    def __init__(self, output_text: str, url: str | None = None):
        self.output_text = output_text
        self.url = url

    def model_dump(self, mode="json"):
        if not self.url:
            return {"output": []}
        return {
            "output": [
                {
                    "content": [
                        {"annotations": [{"type": "url_citation", "url": self.url}]}
                    ]
                }
            ]
        }


class FakeResponses:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeMarket:
    def __init__(self, signals):
        self._signals = signals

    async def signals(self, snapshot):
        return self._signals


def portfolio() -> PortfolioSnapshot:
    snapshot = PortfolioSnapshot(
        id=uuid4(),
        user_id=uuid4(),
        total_value=Decimal("10000"),
        cash=Decimal("0"),
        currency="USD",
        source_hash="test",
        captured_at=datetime.now(UTC),
    )
    snapshot.holdings = [
        Holding(
            ticker="ORCL",
            shares=Decimal("1"),
            current_value=Decimal("200"),
            portfolio_weight=Decimal("0.02"),
            currency="USD",
            user_id=snapshot.user_id,
        )
    ]
    return snapshot


def signal() -> MarketSignal:
    return MarketSignal(
        ticker="ORCL",
        portfolio_weight=0.02,
        regular_price=92,
        regular_change_percent=-8,
        estimated_portfolio_effect=-0.16,
        session="regular",
        observed_at=datetime.now(UTC),
        is_fresh=True,
        rank_score=8,
    )


@pytest.mark.asyncio
async def test_research_sources_stay_internal_to_writer(session) -> None:
    research = {
        "material_change": True,
        "lead_tickers": ["ORCL"],
        "facts": [
            {
                "text": "Oracle raised its infrastructure spending outlook.",
                "kind": "business",
                "tickers": ["ORCL"],
                "confidence": "high",
            }
        ],
        "interpretations": [],
        "thesis_effect": None,
        "next_evidence": None,
        "avoid_claims": [],
        "source_urls": [],
    }
    writer = {
        "text": "Oracle is the main move this morning. Its spending outlook changed the risk picture.",
        "emphasis_phrase": "Oracle is the main move this morning.",
    }
    responses = FakeResponses(
        [
            FakeResponse(json.dumps(research), "https://investor.example.com/result"),
            FakeResponse(json.dumps(writer)),
        ]
    )
    client = SimpleNamespace(responses=responses)
    intelligence = IntelligenceService(
        Settings(_env_file=None, openai_api_key="test"),
        market=FakeMarket([signal()]),
        client=client,
    )
    user = User(id=uuid4(), phone_number="+14165550123")

    draft = await intelligence.morning_brief(user, portfolio(), None, [])

    assert "http" not in draft.text
    assert draft._evidence["source_urls"] == ["https://investor.example.com/result"]
    assert "investor.example.com" not in responses.calls[1]["input"]


@pytest.mark.parametrize(
    "text",
    [
        "Ticker: ORCL moved today.",
        "**Oracle moved today.**",
        "Read https://example.com for more.",
    ],
)
def test_rejects_spammy_or_visible_formatting(text: str) -> None:
    with pytest.raises(IntelligenceUnavailable):
        _validate_draft(MessageDraft(text=text), 3500)


def test_drops_invalid_emphasis_without_changing_text() -> None:
    draft = _validate_draft(
        MessageDraft(text="Oracle moved today.", emphasis_phrase="Missing phrase"),
        3500,
    )
    assert draft.text == "Oracle moved today."
    assert draft.emphasis_phrase is None
