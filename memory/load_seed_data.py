"""Load the synthetic supplier history into Hindsight, one bank per supplier.

Usage:  python memory/load_seed_data.py [--force]

* Records are retained in chronological order with their real historical
  timestamps, so Hindsight can reason about "when".
* Everything loads sequentially (the Hindsight sync client is not thread-safe).
* Idempotent: a marker file (.seed_loaded.json) skips reloading; the retain calls
  also use stable document_ids so a forced reload upserts instead of duplicating.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from agent import ledger  # noqa: E402
from memory import hindsight_store as store  # noqa: E402
from schema import SUPPLIERS  # noqa: E402

MARKER = ROOT / ".seed_loaded.json"


def is_loaded() -> bool:
    """True if seed data was loaded locally, or SEED_LOADED=1 is set (used on cloud deployments
    where the history was already loaded into Hindsight from your own machine)."""
    return MARKER.exists() or os.getenv("SEED_LOADED") == "1"


def _seed_records(supplier_id: str) -> list[tuple]:
    """(date, order, callable-args) for seed files only (not runtime ledger)."""
    data = {k: json.loads((ledger.DATA_DIR / f"{k}.json").read_text(encoding="utf-8"))
            for k in ("quotes", "discounts", "deliveries", "outcomes")}
    models = {k: [ledger._MODELS[k](**r) for r in rows if r["supplier_id"] == supplier_id] for k, rows in data.items()}
    name = ledger.get_supplier(supplier_id).name
    rec = []
    rec += [(q.quote_date, 0, store.retain_quote, q, name) for q in models["quotes"]]
    rec += [(d.discount_date, 1, store.retain_discount, d, name) for d in models["discounts"]]
    rec += [(d.actual_date, 2, store.retain_delivery, d, name) for d in models["deliveries"]]
    rec += [(o.negotiation_date, 3, store.retain_outcome, o, name) for o in models["outcomes"]]
    return sorted(rec, key=lambda r: (r[0], r[1]))


def _load_supplier(supplier_id: str, progress: Callable[[str], None]) -> int:
    sup = ledger.get_supplier(supplier_id)
    store.ensure_bank(supplier_id, sup.name)
    records = _seed_records(supplier_id)
    for i, (_, _, fn, obj, name) in enumerate(records, 1):
        fn(obj, name)
        if i % 5 == 0 or i == len(records):
            progress(f"{sup.name}: retained {i}/{len(records)} memories")
    return len(records)


def load_seed(force: bool = False, progress: Callable[[str], None] = print) -> dict:
    """Load all history. Returns {'skipped': bool, 'counts': {supplier_id: n}}."""
    if is_loaded() and not force:
        progress("Seed data already loaded (use --force to reload).")
        return {"skipped": True, "counts": json.loads(MARKER.read_text())}
    ok, msg = store.health()
    if not ok:
        raise store.HindsightError(f"Cannot load seed data: {msg}")
    counts = {s.supplier_id: _load_supplier(s.supplier_id, progress) for s in SUPPLIERS}  # sequential on purpose
    MARKER.write_text(json.dumps(counts), encoding="utf-8")
    progress(f"Done. Retained {sum(counts.values())} memories across {len(counts)} supplier banks.")
    return {"skipped": False, "counts": counts}


if __name__ == "__main__":
    try:
        load_seed(force="--force" in sys.argv)
    except store.HindsightError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
