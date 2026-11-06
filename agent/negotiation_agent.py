"""The agent loop: parse -> recall -> analyze -> advise -> learn."""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import analysis, ledger, llm  # noqa: E402
from memory import hindsight_store as store  # noqa: E402
from schema import (  # noqa: E402
    AgentRecommendation, Discount, NegotiationOutcome, Quote, Supplier, Tactic,
)

REFLECT_QUESTION = "What negotiation approach has worked best with this supplier?"


@dataclass
class AgentResult:
    recommendation: AgentRecommendation
    quote: Quote
    supplier: Supplier
    memory_enabled: bool
    memories: list[dict] = field(default_factory=list)
    reflection: str = ""
    price: Optional[analysis.PriceAnalysis] = None
    reliability: Optional[analysis.ReliabilityReport] = None
    patterns: Optional[dict] = None
    mode: str = ""
    warnings: list[str] = field(default_factory=list)


def _recall_queries(item: str) -> list[str]:
    return [f"Price history and quotes for {item}, and any discounts given",
            "Late deliveries, damaged goods and quality problems",
            "Negotiation tactics that worked or did not work, and the price results"]


def recall_all(supplier_id: str, item: str) -> tuple[list[dict], list[str]]:
    """Run targeted recalls; dedupe; return (memories, warnings)."""
    warnings: list[str] = []

    def one(q: str) -> list[dict]:
        try:
            return store.recall_supplier(supplier_id, q, k=6)
        except store.HindsightError as e:
            warnings.append(str(e))
            return []

    batches = [one(q) for q in _recall_queries(item)]  # sequential: Hindsight sync client is not thread-safe
    seen, merged = set(), []
    for m in (m for b in batches for m in b):
        if m["text"] not in seen:
            seen.add(m["text"])
            merged.append(m)
    merged.sort(key=lambda m: m.get("date") or "")
    return merged, list(dict.fromkeys(warnings))[:1]


def handle_new_quote(raw_text_or_quote: Union[str, Quote], memory_enabled: bool = True,
                     supplier_id: Optional[str] = None) -> AgentResult:
    """Full pipeline for one incoming quote. Raises ValueError only for unusable input."""
    # 1. input
    if isinstance(raw_text_or_quote, Quote):
        quote = raw_text_or_quote
    else:
        if not raw_text_or_quote or not raw_text_or_quote.strip():
            raise ValueError("Paste a supplier message or use the quick form first.")
        quote = llm.parse_quote_text(raw_text_or_quote, supplier_id)
    supplier = ledger.get_supplier(quote.supplier_id)
    if supplier is None:
        raise ValueError(f"Unknown supplier '{quote.supplier_id}'.")
    result = AgentResult(recommendation=None, quote=quote, supplier=supplier, memory_enabled=memory_enabled)  # type: ignore[arg-type]

    price = rel = patterns = None
    if memory_enabled:
        # 2. recall (Hindsight)
        result.memories, result.warnings = recall_all(supplier.supplier_id, quote.item)
        try:
            result.reflection = store.reflect_supplier(supplier.supplier_id, REFLECT_QUESTION)
        except store.HindsightError as e:
            result.warnings.append(str(e))
        if not result.memories:
            result.warnings.append("No memories recalled for this supplier. Did you load the seed data?")
        # 3. analyze (pure Python)
        hist = ledger.load_for_supplier(supplier.supplier_id)
        price = analysis.price_change(quote, hist["quotes"], hist["outcomes"])
        rel = analysis.reliability_score(hist["deliveries"])
        patterns = analysis.discount_patterns(hist["discounts"], hist["outcomes"])
        result.price, result.reliability, result.patterns = price, rel, patterns

    # 4. advise
    result.recommendation, result.mode = llm.advise(
        quote, supplier, result.memories, result.reflection, price, rel, patterns, memory_enabled)
    if result.mode == "rule-based":
        result.warnings.append("LLM unavailable or invalid output: showing rule-based advice.")
    return result


def log_outcome(quote: Quote, tactic: Tactic, worked: bool, final_price: float,
                notes: Optional[str] = None) -> dict:
    """5. Learn: retain the quote + negotiation outcome (+ discount) so next time is smarter."""
    supplier = ledger.get_supplier(quote.supplier_id)
    if supplier is None:
        raise ValueError(f"Unknown supplier '{quote.supplier_id}'.")
    outcome = NegotiationOutcome(supplier_id=quote.supplier_id, negotiation_date=quote.quote_date,
                                 quote_id=quote.quote_id, tactic_used=tactic, worked=worked,
                                 original_price=quote.unit_price, final_price=final_price, notes=notes or None)
    discount = None
    if worked and final_price < quote.unit_price:
        pct = round((quote.unit_price - final_price) / quote.unit_price * 100, 1)
        discount = Discount(supplier_id=quote.supplier_id, percent=pct, discount_date=quote.quote_date,
                            reason=f"{tactic.value.replace('_', ' ')} negotiation")
    # local ledger first (so analysis is current even if Hindsight is down)
    ledger.append("quotes", quote)
    ledger.append("outcomes", outcome)
    if discount:
        ledger.append("discounts", discount)
    status = {"retained": 0, "error": None}
    try:
        store.retain_quote(quote, supplier.name)
        store.retain_outcome(outcome, supplier.name)
        status["retained"] = 2
        if discount:
            store.retain_discount(discount, supplier.name)
            status["retained"] = 3
    except store.HindsightError as e:
        status["error"] = str(e)
    return status
