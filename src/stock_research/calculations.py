"""Deterministic financial calculations used by research reports."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class CalculationResult:
    calculation_type: str
    formula_version: str
    inputs: dict[str, Any]
    outputs: dict[str, float]


def _require_finite(name: str, value: float) -> float:
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def cagr(start_value: float, end_value: float, years: float) -> CalculationResult:
    start_value = _require_finite("start_value", float(start_value))
    end_value = _require_finite("end_value", float(end_value))
    years = _require_finite("years", float(years))
    if start_value <= 0 or end_value < 0 or years <= 0:
        raise ValueError("CAGR requires start > 0, end >= 0 and years > 0")
    result = (end_value / start_value) ** (1 / years) - 1
    return CalculationResult("cagr", "cagr-v1", {"start_value": start_value, "end_value": end_value, "years": years}, {"cagr": result})


def ratio(numerator: float, denominator: float, name: str = "ratio") -> CalculationResult:
    numerator = _require_finite("numerator", float(numerator))
    denominator = _require_finite("denominator", float(denominator))
    if denominator == 0:
        raise ValueError("ratio denominator must not be zero")
    return CalculationResult(name, "ratio-v1", {"numerator": numerator, "denominator": denominator}, {name: numerator / denominator})


def free_cash_flow(operating_cash_flow: float, capital_expenditure: float) -> CalculationResult:
    """Calculate FCF assuming capital expenditure is provided as a positive outflow."""
    operating_cash_flow = _require_finite("operating_cash_flow", float(operating_cash_flow))
    capital_expenditure = _require_finite("capital_expenditure", float(capital_expenditure))
    return CalculationResult(
        "free_cash_flow",
        "fcf-v1",
        {"operating_cash_flow": operating_cash_flow, "capital_expenditure": capital_expenditure},
        {"free_cash_flow": operating_cash_flow - capital_expenditure},
    )


def net_cash(cash: float, debt: float) -> CalculationResult:
    cash = _require_finite("cash", float(cash))
    debt = _require_finite("debt", float(debt))
    return CalculationResult("net_cash", "net-cash-v1", {"cash": cash, "debt": debt}, {"net_cash": cash - debt})


def dcf(
    base_fcf: float,
    growth_rates: Iterable[float],
    discount_rate: float,
    terminal_growth: float,
    net_cash: float = 0.0,
    shares: float | None = None,
) -> CalculationResult:
    """Calculate a transparent multi-period DCF.

    `base_fcf` is the latest available free cash flow.  Growth rates are
    applied sequentially for forecast years.  All rates are decimals.
    """

    base_fcf = _require_finite("base_fcf", float(base_fcf))
    discount_rate = _require_finite("discount_rate", float(discount_rate))
    terminal_growth = _require_finite("terminal_growth", float(terminal_growth))
    rates = [_require_finite("growth_rate", float(rate)) for rate in growth_rates]
    if base_fcf <= 0 or not rates:
        raise ValueError("DCF requires positive base FCF and at least one forecast year")
    if discount_rate <= terminal_growth or discount_rate <= -1:
        raise ValueError("discount rate must be greater than terminal growth")
    if shares is not None and shares <= 0:
        raise ValueError("shares must be positive")

    forecast: list[float] = []
    current = base_fcf
    for rate in rates:
        current *= 1 + rate
        forecast.append(current)
    pv_forecast = sum(value / ((1 + discount_rate) ** (index + 1)) for index, value in enumerate(forecast))
    terminal_value = forecast[-1] * (1 + terminal_growth) / (discount_rate - terminal_growth)
    pv_terminal = terminal_value / ((1 + discount_rate) ** len(forecast))
    enterprise_value = pv_forecast + pv_terminal
    equity_value = enterprise_value + net_cash
    outputs = {
        "pv_forecast": pv_forecast,
        "terminal_value": terminal_value,
        "pv_terminal": pv_terminal,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
    }
    if shares is not None:
        outputs["value_per_share"] = equity_value / shares
    return CalculationResult(
        "dcf",
        "dcf-v1",
        {
            "base_fcf": base_fcf,
            "growth_rates": rates,
            "discount_rate": discount_rate,
            "terminal_growth": terminal_growth,
            "net_cash": float(net_cash),
            **({"shares": float(shares)} if shares is not None else {}),
        },
        outputs,
    )
