"""Market-data provider seam and a small Yahoo Finance EOD adapter.

The adapter is intentionally isolated from the workflow.  A licensed HK
provider can implement the same protocol later without changing research
logic.  Yahoo data is suitable for a prototype only; callers must preserve
the provider and timestamp metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from typing import Any, Callable
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class PriceBar:
    symbol: str
    trading_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int | None
    provider: str
    fetched_at: datetime


class MarketDataError(RuntimeError):
    """Raised when a market provider returns an unusable response."""


class YahooFinanceProvider:
    """Fetch daily bars through Yahoo Finance's public chart endpoint."""

    provider_name = "yahoo_chart"

    def __init__(
        self,
        opener: Callable[..., Any] = urlopen,
        user_agent: str = "ai-stock-research/0.1",
    ) -> None:
        self._opener = opener
        self._user_agent = user_agent

    def get_daily_bars(self, symbol: str, start: date, end: date) -> list[PriceBar]:
        if not symbol.strip():
            raise ValueError("symbol must not be empty")
        if end <= start:
            raise ValueError("end must be after start")

        # Yahoo's period2 is exclusive, so add one day to include `end`.
        period1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
        period2 = int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp()) + 86400
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{quote(symbol, safe='')}?period1={period1}&period2={period2}&interval=1d"
            "&events=div%2Csplits&includeAdjustedClose=true"
        )
        request = Request(url, headers={"User-Agent": self._user_agent, "Accept": "application/json"})
        try:
            with self._opener(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # network and malformed JSON are provider failures
            raise MarketDataError(f"failed to fetch {symbol} from Yahoo Finance: {exc}") from exc

        result = ((payload.get("chart") or {}).get("result") or [None])[0]
        error = (payload.get("chart") or {}).get("error")
        if result is None:
            message = (error or {}).get("description") if isinstance(error, dict) else "empty result"
            raise MarketDataError(f"Yahoo Finance returned no data for {symbol}: {message}")

        timestamps = result.get("timestamp") or []
        quote_data = ((result.get("indicators") or {}).get("quote") or [None])[0] or {}
        fields = {name: quote_data.get(name) or [] for name in ("open", "high", "low", "close", "volume")}
        fetched_at = datetime.now(timezone.utc)
        bars: list[PriceBar] = []
        for index, timestamp in enumerate(timestamps):
            values = {name: values[index] if index < len(values) else None for name, values in fields.items()}
            if any(values[name] is None for name in ("open", "high", "low", "close")):
                # Suspended/non-trading rows can contain null OHLC values.
                continue
            trading_date = datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
            bars.append(
                PriceBar(
                    symbol=symbol,
                    trading_date=trading_date,
                    open=float(values["open"]),
                    high=float(values["high"]),
                    low=float(values["low"]),
                    close=float(values["close"]),
                    volume=int(values["volume"]) if values["volume"] is not None else None,
                    provider=self.provider_name,
                    fetched_at=fetched_at,
                )
            )
        return bars

