from datetime import date

import httpx

from app.config import Settings


class MarketDataClient:
    """Minimal FMP adapter for deterministic prices and earnings dates."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client

    async def quotes(self, tickers: list[str]) -> dict[str, dict]:
        symbols = sorted({ticker.upper() for ticker in tickers if ticker})
        if not symbols or not self.settings.fmp_api_key:
            return {}
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(base_url="https://financialmodelingprep.com", timeout=20)
        try:
            response = await client.get(
                f"/stable/batch-quote?symbols={','.join(symbols)}&apikey={self.settings.fmp_api_key}"
            )
            response.raise_for_status()
            rows = response.json()
            return {str(row.get("symbol", "")).upper(): row for row in rows if row.get("symbol")}
        finally:
            if own_client:
                await client.aclose()

    async def earnings_calendar(self, start: date, end: date) -> list[dict]:
        if not self.settings.fmp_api_key:
            return []
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(base_url="https://financialmodelingprep.com", timeout=20)
        try:
            response = await client.get(
                "/stable/earnings-calendar",
                params={"from": start.isoformat(), "to": end.isoformat(), "apikey": self.settings.fmp_api_key},
            )
            response.raise_for_status()
            value = response.json()
            return value if isinstance(value, list) else []
        finally:
            if own_client:
                await client.aclose()

