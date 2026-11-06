"""Pure-Python numeric analysis (no LLM). Exact numbers the agent can trust."""
from __future__ import annotations

import re
import sys
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from statistics import mean
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from schema import Delivery, Discount, NegotiationOutcome, Quote, Tactic  # noqa: E402

SPIKE_PCT = 8.0      # > +8% vs last quote is a spike
DROP_PCT = -3.0      # < -3% vs last quote is a drop


# ------------------------------------------------------------------ price
@dataclass
class PriceAnalysis:
    item: str
    new_price: float
    n_history: int
    last_price: Optional[float] = None      # price actually paid (negotiated) if known, else quoted
    last_quoted: Optional[float] = None     # what the supplier originally asked
    last_was_negotiated: bool = False
    last_date: Optional[str] = None
    avg_3m: Optional[float] = None
    low_12m: Optional[float] = None
    pct_vs_last: Optional[float] = None
    pct_vs_3m_avg: Optional[float] = None
    pct_vs_12m_low: Optional[float] = None
    flag: str = "normal"
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _tokens(item: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", item.lower()))


def same_item(a: str, b: str) -> bool:
    """True if two item descriptions refer to the same SKU (token overlap)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.5


def _pct(new: float, ref: Optional[float]) -> Optional[float]:
    return None if not ref else round((new - ref) / ref * 100, 1)


def price_change(new_quote: Quote, history: list[Quote],
                 outcomes: Optional[list[NegotiationOutcome]] = None) -> PriceAnalysis:
    """Compare a new quote with earlier prices of the same item and supplier.

    If a negotiation worked, the price actually paid (final_price) is used instead of the
    price originally asked, so the baseline reflects what you really pay.
    """
    paid = {o.quote_id: o.final_price for o in (outcomes or []) if o.worked}

    def eff(q: Quote) -> float:
        return paid.get(q.quote_id, q.unit_price)

    prior = sorted(
        (q for q in history
         if q.supplier_id == new_quote.supplier_id
         and q.quote_id != new_quote.quote_id
         and q.quote_date <= new_quote.quote_date
         and same_item(q.item, new_quote.item)),
        key=lambda q: q.quote_date)
    res = PriceAnalysis(item=new_quote.item, new_price=new_quote.unit_price, n_history=len(prior))
    if not prior:
        res.summary = "No earlier quote for this item from this supplier, so no price comparison is possible."
        return res

    d = new_quote.quote_date
    last = prior[-1]
    w3 = [eff(q) for q in prior if q.quote_date >= d - timedelta(days=90)]
    w12 = [eff(q) for q in prior if q.quote_date >= d - timedelta(days=365)] or [eff(last)]
    res.last_price, res.last_date = eff(last), last.quote_date.isoformat()
    res.last_quoted, res.last_was_negotiated = last.unit_price, last.quote_id in paid
    res.avg_3m = round(mean(w3), 2) if w3 else None
    res.low_12m = min(w12)
    res.pct_vs_last = _pct(new_quote.unit_price, res.last_price)
    res.pct_vs_3m_avg = _pct(new_quote.unit_price, res.avg_3m)
    res.pct_vs_12m_low = _pct(new_quote.unit_price, res.low_12m)

    if res.pct_vs_last > SPIKE_PCT or (res.pct_vs_3m_avg or 0) > 10:
        res.flag = "spike"
    elif res.pct_vs_last < DROP_PCT:
        res.flag = "drop"
    basis = (f"last negotiated price (Rs {res.last_price:.2f} on {res.last_date}; they had asked Rs {last.unit_price:.2f})"
             if res.last_was_negotiated else f"last quote (Rs {last.unit_price:.2f} on {res.last_date})")
    res.summary = (f"Rs {new_quote.unit_price:.2f} is {res.pct_vs_last:+.1f}% vs the {basis}, "
                   f"{res.pct_vs_12m_low:+.1f}% vs the 12-month low (Rs {res.low_12m:.2f}).")
    return res


# ------------------------------------------------------------ reliability
@dataclass
class ReliabilityReport:
    score: float
    summary: str
    n_orders: int = 0
    late_count: int = 0
    avg_days_late: float = 0.0
    issue_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def reliability_score(deliveries: list[Delivery]) -> ReliabilityReport:
    """0-100 score: 55% on-time rate, 25% lateness severity, 20% quality."""
    n = len(deliveries)
    if n == 0:
        return ReliabilityReport(50.0, "No delivery history yet (neutral score).")
    late = [d.days_late for d in deliveries if d.days_late > 0]
    issues = [d for d in deliveries if d.quality_issue]
    on_time = (n - len(late)) / n
    avg_late_all = sum(late) / n
    score = 100 * (0.55 * on_time + 0.25 * (1 - min(avg_late_all / 7, 1)) + 0.20 * (1 - len(issues) / n))
    avg_late = round(sum(late) / len(late), 1) if late else 0.0
    parts = [f"late on {len(late)} of {n} orders" + (f", avg {avg_late} days" if late else "")]
    if issues:
        parts.append(f"quality issues on {len(issues)} orders")
    return ReliabilityReport(round(score, 1), "; ".join(parts), n, len(late), avg_late, len(issues))


# ------------------------------------------------------ negotiation patterns
def discount_patterns(discounts: list[Discount], outcomes: list[NegotiationOutcome]) -> dict:
    """Which tactics worked per supplier, success rate and average saving."""
    by: dict[str, dict] = {}
    for o in outcomes:
        t = o.tactic_used.value if isinstance(o.tactic_used, Tactic) else str(o.tactic_used)
        row = by.setdefault(t, {"attempts": 0, "successes": 0, "savings": []})
        row["attempts"] += 1
        if o.worked:
            row["successes"] += 1
            if o.original_price:
                row["savings"].append((o.original_price - o.final_price) / o.original_price * 100)
    table = {}
    for t, r in by.items():
        table[t] = {"attempts": r["attempts"], "successes": r["successes"],
                    "success_rate": round(r["successes"] / r["attempts"] * 100),
                    "avg_saving_pct": round(mean(r["savings"]), 1) if r["savings"] else 0.0}
    ranked = sorted((t for t in table if t != Tactic.NONE.value),
                    key=lambda t: (table[t]["success_rate"], table[t]["successes"], table[t]["avg_saving_pct"]),
                    reverse=True)
    best = ranked[0] if ranked and table[ranked[0]]["successes"] > 0 else None
    worst = [t for t in ranked if table[t]["successes"] == 0]
    return {"by_tactic": table, "best_tactic": best, "failed_tactics": worst,
            "num_discounts": len(discounts),
            "avg_discount_pct": round(mean(d.percent for d in discounts), 1) if discounts else 0.0}


def patterns_summary(p: dict) -> str:
    """One-paragraph plain-English view of discount_patterns() output."""
    if not p["by_tactic"]:
        return "No negotiation history."
    lines = [f"{t}: worked {v['successes']}/{v['attempts']} times"
             + (f", avg saving {v['avg_saving_pct']}%" if v["successes"] else "")
             for t, v in p["by_tactic"].items()]
    return "; ".join(lines)


# ------------------------------------------------------------------ charts
def price_trend_df(quotes: list[Quote], supplier_names: Optional[dict[str, str]] = None) -> pd.DataFrame:
    """Quotes as a tidy DataFrame, ready for plotly."""
    rows = [{"quote_date": pd.Timestamp(q.quote_date), "supplier_id": q.supplier_id,
             "supplier": (supplier_names or {}).get(q.supplier_id, q.supplier_id),
             "item": q.item, "quantity": q.quantity, "unit_price": q.unit_price} for q in quotes]
    cols = ["quote_date", "supplier_id", "supplier", "item", "quantity", "unit_price"]
    df = pd.DataFrame(rows, columns=cols)
    return df.sort_values("quote_date").reset_index(drop=True)
