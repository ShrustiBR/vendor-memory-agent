"""All Hindsight (Vectorize) retain / recall / reflect code lives here.

One memory bank per supplier (see schema.bank_id_for). Works with Hindsight
Cloud or a local open-source instance, selected purely via env vars:

    HINDSIGHT_BASE_URL  (default http://localhost:8888)
    HINDSIGHT_API_KEY   (required for Cloud, empty for local)
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from schema import (  # noqa: E402
    Delivery, Discount, NegotiationOutcome, Quote, bank_id_for,
    delivery_to_memory_text, discount_to_memory_text, outcome_to_memory_text,
    quote_to_memory_text,
)

BANK_MISSION = (
    "Remember everything about this supplier's pricing, discounts, delivery reliability, "
    "and negotiation outcomes (which tactics worked and which did not) to help a "
    "procurement manager negotiate better prices and terms."
)
BANK_DISPOSITION = {"skepticism": 4, "literalism": 4, "empathy": 2}

_banks_ready: set[str] = set()


class HindsightError(RuntimeError):
    """Raised with a user-friendly message when Hindsight cannot be used."""


# ----------------------------------------------------------------- plumbing
def _friendly(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    status = getattr(exc, "status", None)
    if status in (401, 402, 403):
        hint = {401: "API key missing/invalid", 402: "out of credits (add promo MEMHACK99 under Billing)",
                403: "forbidden: key not allowed, wrong HINDSIGHT_BASE_URL, or no credits/billing set up"}[status]
        return f"HTTP {status} - {hint}. Server said: {str(body)[:300]}"
    msg = str(exc)
    for code, hint in (("401", "invalid Hindsight API key"), ("402", "out of Hindsight credits (add promo MEMHACK99 in billing)"),
                       ("404", "address not found: check HINDSIGHT_BASE_URL (Cloud API is https://api.hindsight.vectorize.io, not the ui. dashboard address)"), ("Connection", "cannot reach the Hindsight server (check HINDSIGHT_BASE_URL)")):
        if code in msg:
            return f"{hint} [{msg[:120]}]"
    return msg[:200]


def _new_client():
    """Build a fresh Hindsight client. One per call: the SDK's sync wrapper is bound to an
    asyncio event loop/thread, so sharing a client across threads breaks (aiohttp errors)."""
    try:
        from hindsight_client import Hindsight
    except ImportError as e:
        raise HindsightError("hindsight-client is not installed. Run: pip install -r requirements.txt") from e
    kwargs = {"base_url": os.getenv("HINDSIGHT_BASE_URL", "http://localhost:8888"), "timeout": 60.0}
    if os.getenv("HINDSIGHT_API_KEY"):
        kwargs["api_key"] = os.environ["HINDSIGHT_API_KEY"]
    return Hindsight(**kwargs)


def _run(fn: Callable):
    """Run fn(client) with a fresh client and always close it."""
    client = _new_client()
    try:
        return fn(client)
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def _with_retries(label: str, fn: Callable,  # fn(client)
                   retries: int = 2, delay: float = 1.5):
    """Run fn(); retry `retries` times; raise HindsightError with a clear message."""
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return _run(fn)
        except HindsightError:
            raise
        except Exception as e:  # noqa: BLE001 - SDK raises varied error types
            last = e
            if attempt < retries:
                time.sleep(delay * (attempt + 1))
    raise HindsightError(f"Hindsight {label} failed after {retries + 1} attempts: {_friendly(last)}")


def health() -> tuple[bool, str]:
    """(ok, message) for the UI connection badge."""
    try:
        def check(client):
            if hasattr(client, "get_version"):
                try:
                    return f"Connected (API {getattr(client.get_version(), 'api_version', '?')})"
                except Exception as e:  # noqa: BLE001
                    if getattr(e, "status", None) == 404:  # version endpoint missing; real calls will tell
                        return "Configured (version check unavailable)"
                    raise
            return "Client configured"
        return True, _run(check)
    except Exception as e:  # noqa: BLE001
        return False, _friendly(e)


# -------------------------------------------------------------------- banks
def ensure_bank(supplier_id: str, supplier_name: str = "") -> str:
    """Create the supplier's bank (with mission/disposition) if needed."""
    bank = bank_id_for(supplier_id)
    if bank in _banks_ready:
        return bank
    name = f"Vendor memory: {supplier_name or supplier_id}"

    def create(client):
        try:
            client.create_bank(bank_id=bank, name=name, mission=BANK_MISSION, disposition=BANK_DISPOSITION)
        except TypeError:  # older client without mission/disposition kwargs
            client.create_bank(bank_id=bank, name=name)

    try:
        _run(create)
    except HindsightError:
        raise
    except Exception as e:  # noqa: BLE001 - usually "already exists"
        if getattr(e, "status", None) in (401, 402, 403):
            raise HindsightError(f"Cannot create bank '{bank}': {_friendly(e)}") from e
    _banks_ready.add(bank)
    return bank


# ------------------------------------------------------------------- retain
def _at(d: date) -> datetime:
    return datetime.combine(d, dtime(10, 0), tzinfo=timezone.utc)


def _retain(supplier_id: str, supplier_name: str, text: str, context: str, when: date, doc_id: str) -> None:
    bank = ensure_bank(supplier_id, supplier_name)
    _with_retries("retain", lambda c: c.retain(
        bank_id=bank, content=text, context=context, timestamp=_at(when), document_id=doc_id))


def retain_quote(q: Quote, supplier_name: str) -> None:
    _retain(q.supplier_id, supplier_name, quote_to_memory_text(q, supplier_name),
            "supplier quotation", q.quote_date, f"quote:{q.quote_id}")


def retain_discount(d: Discount, supplier_name: str) -> None:
    _retain(d.supplier_id, supplier_name, discount_to_memory_text(d, supplier_name),
            "supplier discount", d.discount_date, f"discount:{d.supplier_id}:{d.discount_date}:{d.percent}")


def retain_delivery(d: Delivery, supplier_name: str) -> None:
    _retain(d.supplier_id, supplier_name, delivery_to_memory_text(d, supplier_name),
            "delivery record", d.actual_date, f"delivery:{d.order_id}")


def retain_outcome(o: NegotiationOutcome, supplier_name: str) -> None:
    _retain(o.supplier_id, supplier_name, outcome_to_memory_text(o, supplier_name),
            "negotiation outcome", o.negotiation_date, f"outcome:{o.quote_id}:{o.tactic_used.value}")


# ------------------------------------------------------------------- recall
def recall_supplier(supplier_id: str, query: str, k: int = 6) -> list[dict]:
    """Return up to k memories as dicts: {text, type, date}."""
    bank = bank_id_for(supplier_id)
    res = _with_retries("recall", lambda c: c.recall(bank_id=bank, query=query, budget="mid"))
    out = []
    for r in (getattr(res, "results", None) or [])[:k]:
        when = next((getattr(r, a) for a in ("occurred_start", "mentioned_at", "event_date", "timestamp")
                     if getattr(r, a, None)), None)
        out.append({"text": getattr(r, "text", str(r)), "type": str(getattr(r, "type", "") or ""),
                    "date": str(when)[:10] if when else ""})
    return out


# ------------------------------------------------------------------ reflect
def reflect_supplier(supplier_id: str, question: str) -> str:
    """Ask Hindsight to synthesise an insight over the supplier's memories."""
    bank = bank_id_for(supplier_id)
    ans = _with_retries("reflect", lambda c: c.reflect(
        bank_id=bank, query=question, budget="low",
        context="Advising a procurement manager about to negotiate a supplier quote"))
    return (getattr(ans, "text", "") or "").strip()
