"""Basic tests for agent/analysis.py. Run: pytest -q"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.analysis import discount_patterns, price_change, price_trend_df, reliability_score  # noqa: E402
from schema import Delivery, Discount, NegotiationOutcome, Quote, Tactic  # noqa: E402

ITEM = "Corrugated Box 12x10x8 (5-ply, 150 GSM)"


def q(qid, price, d, item=ITEM):
    return Quote(quote_id=qid, supplier_id="SUP-001", item=item, quantity=3000, unit_price=price, quote_date=d)


HISTORY = [q("a", 17.0, date(2026, 6, 1)), q("b", 17.2, date(2026, 8, 1)), q("c", 17.4, date(2026, 9, 1)),
           q("x", 400.0, date(2026, 9, 5), item="Bubble Wrap Roll 1m x 50m")]


def test_spike_flag():
    r = price_change(q("new", 19.5, date(2026, 11, 3)), HISTORY)
    assert r.flag == "spike" and r.pct_vs_last == 12.1 and r.n_history == 3


def test_normal_and_drop():
    assert price_change(q("new", 17.5, date(2026, 11, 3)), HISTORY).flag == "normal"
    assert price_change(q("new", 16.0, date(2026, 11, 3)), HISTORY).flag == "drop"


def test_no_history_is_safe():
    r = price_change(q("new", 20.0, date(2026, 11, 3), item="Poly Mailer 10x14in"), HISTORY)
    assert r.flag == "normal" and r.n_history == 0 and r.pct_vs_last is None


def _d(late, issue=None):
    return Delivery(supplier_id="SUP-001", order_id="PO", promised_date=date(2026, 1, 1),
                    actual_date=date(2026, 1, 1 + late), quality_issue=issue)


def test_reliability_bounds_and_summary():
    perfect = reliability_score([_d(0)] * 5)
    bad = reliability_score([_d(5, "damaged")] * 5)
    assert perfect.score == 100 and bad.score < 40
    assert "late on 5 of 5" in bad.summary
    assert reliability_score([]).score == 50


def test_discount_patterns():
    def o(t, worked, final):
        return NegotiationOutcome(supplier_id="SUP-001", negotiation_date=date(2026, 5, 1), quote_id="a",
                                  tactic_used=t, worked=worked, original_price=100.0, final_price=final)
    outs = [o(Tactic.DELIVERY_PENALTY, True, 96), o(Tactic.DELIVERY_PENALTY, True, 94),
            o(Tactic.VOLUME_COMMITMENT, False, 100)]
    p = discount_patterns([Discount(supplier_id="SUP-001", percent=4, reason="x", discount_date=date(2026, 5, 1))], outs)
    assert p["best_tactic"] == "delivery_penalty_clause"
    assert p["by_tactic"]["delivery_penalty_clause"]["avg_saving_pct"] == 5.0
    assert p["failed_tactics"] == ["volume_commitment"]


def test_trend_df():
    df = price_trend_df(HISTORY)
    assert list(df["unit_price"])[0] == 17.0 and len(df) == 4


def test_uses_negotiated_price_as_baseline():
    hist = [q("a", 50.0, date(2026, 9, 1))]
    out = [NegotiationOutcome(supplier_id="SUP-001", negotiation_date=date(2026, 9, 3), quote_id="a",
                              tactic_used=Tactic.COMPETING_QUOTE, worked=True, original_price=50.0, final_price=48.0)]
    r = price_change(q("new", 49.0, date(2026, 11, 3)), hist, out)
    assert r.last_price == 48.0 and r.last_quoted == 50.0 and r.pct_vs_last == 2.1
    assert price_change(q("new", 49.0, date(2026, 11, 3)), hist).pct_vs_last == -2.0   # old behaviour
