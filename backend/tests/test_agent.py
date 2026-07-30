import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agent import _checkpoint_database_uri, _evidence, agent_runtime
from app.config import Settings
from app.intelligence import IntelligenceUnavailable, _validate_draft
from app.schemas import MessageDraft


class FakeMarket:
    pass


@pytest.mark.asyncio
async def test_runtime_builds_stateful_agent(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        openai_api_key="test",
        agent_checkpoint_path=str(tmp_path / "agent.db"),
    )
    async with agent_runtime(settings, FakeMarket()) as agent:
        assert {"model", "tools"}.issubset(agent.graph.nodes)


def test_collects_tools_errors_and_sources_as_internal_evidence() -> None:
    messages = [
        AIMessage(
            content=[
                {
                    "type": "text",
                    "text": "Evidence",
                    "annotations": [
                        {"type": "url_citation", "url": "https://example.com/source"}
                    ],
                }
            ],
            tool_calls=[
                {"name": "get_market_signals", "args": {}, "id": "call-1", "type": "tool_call"}
            ],
        ),
        ToolMessage(
            content='{"error":"market_data_unavailable"}',
            tool_call_id="call-1",
            name="get_market_signals",
        ),
    ]
    evidence = _evidence(messages)
    assert evidence["tools"] == ["get_market_signals"]
    assert evidence["errors"] == ["market_data_unavailable"]
    assert evidence["source_urls"] == ["https://example.com/source"]


def test_checkpoint_uri_is_psycopg_compatible() -> None:
    uri = _checkpoint_database_uri(
        "postgresql+asyncpg://user:pass@example.com/db?ssl=require"
    )

    assert uri == "postgresql://user:pass@example.com/db?sslmode=require"


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
