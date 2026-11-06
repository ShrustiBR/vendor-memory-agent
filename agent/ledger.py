"""Structured ledger: seed JSON files + records logged at runtime.

Hindsight holds the *memory* (what the agent recalls and reasons over).
The ledger feeds the pure-Python numeric analysis (price %, reliability score)
so those numbers are exact and never hallucinated.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from schema import (  # noqa: E402
    SUPPLIERS, Delivery, Discount, NegotiationOutcome, Quote, Supplier,
)

DATA_DIR = ROOT / "data"
RUNTIME_FILE = DATA_DIR / "runtime_ledger.json"

_MODELS = {"quotes": Quote, "discounts": Discount, "deliveries": Delivery, "outcomes": NegotiationOutcome}


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def get_supplier(supplier_id: str) -> Optional[Supplier]:
    return next((s for s in SUPPLIERS if s.supplier_id == supplier_id), None)


def find_supplier_by_text(text: str) -> Optional[Supplier]:
    """Match a supplier by name (or first word of its name) mentioned in free text."""
    low = text.lower()
    for s in SUPPLIERS:
        if s.name.lower() in low or s.name.split()[0].lower() in low:
            return s
    return None


def _runtime() -> dict[str, list[dict]]:
    if RUNTIME_FILE.exists():
        try:
            return json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {k: [] for k in _MODELS}


def load(kind: str) -> list:
    """Load seed + runtime records of one kind as pydantic models."""
    rows = _read(DATA_DIR / f"{kind}.json") + _runtime().get(kind, [])
    return [_MODELS[kind](**r) for r in rows]


def load_for_supplier(supplier_id: str) -> dict[str, list]:
    return {k: [r for r in load(k) if r.supplier_id == supplier_id] for k in _MODELS}


def append(kind: str, record) -> None:
    """Persist a runtime record (e.g. a quote or outcome logged from the UI)."""
    data = _runtime()
    data.setdefault(kind, []).append(record.model_dump(mode="json"))
    RUNTIME_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def reset_runtime() -> None:
    """Forget everything logged during demos (useful to re-run the demo)."""
    if RUNTIME_FILE.exists():
        RUNTIME_FILE.unlink()
