"""Vendor Memory & Negotiation Agent - Streamlit UI.  Run: streamlit run app/streamlit_app.py"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from app import auth  # noqa: E402
from agent import analysis, ledger, llm, negotiation_agent as agent  # noqa: E402
from agent.prompts import CANONICAL_SKUS  # noqa: E402
from memory import hindsight_store as store, load_seed_data  # noqa: E402
from schema import SUPPLIERS, Quote, Tactic  # noqa: E402

st.set_page_config(page_title="Vendor Memory Agent", page_icon="🧠", layout="wide")

NAMES = {s.supplier_id: s.name for s in SUPPLIERS}
DEMO = json.loads((ROOT / "data" / "demo_quotes.json").read_text(encoding="utf-8")) \
    if (ROOT / "data" / "demo_quotes.json").exists() else []
AUTO = "Auto-detect from message"

ss = st.session_state
ss.setdefault("raw_text", "")
ss.setdefault("result", None)
ss.setdefault("compare", None)
ss.setdefault("last_quote", None)
ss.setdefault("conn", None)
ss.setdefault("sig", None)
ss.setdefault("error", None)


# ------------------------------------------------------------- login gate
def auth_gate() -> None:
    """Show Log in / Create account until the user is signed in. Set AUTH_REQUIRED=0 to skip locally."""
    if os.getenv("AUTH_REQUIRED", "1") == "0":
        ss.setdefault("user", {"username": "local", "full_name": "Local user"})
    if ss.get("user"):
        return
    left, mid, right = st.columns([1, 2, 1])
    with mid:
        st.title("🧠 Vendor Memory Agent")
        st.caption("Procurement negotiation agent that remembers every supplier. Sign in to continue.")
        t_login, t_signup = st.tabs(["Log in", "Create account"])
        with t_login:
            with st.form("login_form"):
                u = st.text_input("Username")
                p = st.text_input("Password", type="password")
                if st.form_submit_button("Log in", type="primary"):
                    try:
                        ss.user = auth.authenticate(u, p)
                        st.rerun()
                    except auth.AuthError as e:
                        st.error(str(e))
        with t_signup:
            with st.form("signup_form"):
                name = st.text_input("Full name (optional)")
                u = st.text_input("Choose a username", help="3-30 letters, numbers, . - _")
                p1 = st.text_input("Choose a password", type="password", help="At least 8 characters")
                p2 = st.text_input("Confirm password", type="password")
                if st.form_submit_button("Create account", type="primary"):
                    try:
                        if p1 != p2:
                            raise auth.AuthError("Passwords do not match.")
                        ss.user = auth.create_user(u, p1, name)
                        st.rerun()
                    except auth.AuthError as e:
                        st.error(str(e))
    st.stop()


auth_gate()


# ----------------------------------------------------------------- helpers
def load_demo() -> None:
    choice = ss.get("demo_choice")
    if choice and choice != "-":
        d = next(x for x in DEMO if f"Step {x['step']}: {x['title']}" == choice)
        ss.raw_text = d["raw_text"]
        ss.supplier_choice = d["supplier_id"]
        ss.input_mode = "Paste message"


def build_input():
    """Return either raw text or a Quote from the quick form."""
    sid = ss.get("supplier_choice", AUTO)
    if ss.get("input_mode") == "Quick form":
        if sid == AUTO:
            raise ValueError("Pick a supplier in the sidebar to use the quick form.")
        return Quote(quote_id=f"QT-NEW-{int(time.time())}", supplier_id=sid, item=ss.qf_item,
                     quantity=int(ss.qf_qty), unit_price=float(ss.qf_price), quote_date=ss.qf_date,
                     valid_until=ss.qf_date + timedelta(days=15))
    return ss.raw_text


def input_signature() -> tuple:
    """A fingerprint of everything the user can change. When it changes, results are stale."""
    sid = ss.get("supplier_choice", AUTO)
    if ss.get("input_mode") == "Quick form":
        return ("form", sid, ss.get("qf_item"), ss.get("qf_qty"), ss.get("qf_price"), str(ss.get("qf_date")))
    return ("text", sid, (ss.get("raw_text") or "").strip())


def refresh(force: bool = False) -> None:
    """Parse the quote ONCE, then run the agent with memory OFF and ON on that same quote.

    Runs automatically when the input changed (if Auto-update is on) or when forced by a button.
    """
    sig = input_signature()
    if not force and (not ss.get("auto_run", True) or sig == ss.sig):
        return
    if sig[0] == "text" and not sig[2]:
        return  # nothing pasted yet
    ss.sig, ss.error = sig, None
    try:
        with st.spinner("Recalling memories and thinking..."):
            inp = build_input()
            sid = ss.get("supplier_choice", AUTO)
            quote = inp if isinstance(inp, Quote) else llm.parse_quote_text(inp, None if sid == AUTO else sid)
            off = agent.handle_new_quote(quote, memory_enabled=False)
            on = agent.handle_new_quote(quote, memory_enabled=True)
        ss.compare, ss.last_quote = (off, on), quote
    except ValueError as e:
        ss.error = str(e)
    except Exception as e:  # noqa: BLE001
        ss.error = f"Something went wrong: {e}"


def flag_badge(flag: str) -> str:
    return {"spike": "🔴 Spike", "drop": "🟢 Drop", "normal": "🟡 Normal"}.get(flag, "⚪ n/a")


def render_result(r: "agent.AgentResult", compact: bool = False) -> None:
    rec = r.recommendation
    c1, c2, c3 = st.columns(3)
    if r.memory_enabled:
        c1.metric("Price change vs last quote", f"{rec.price_change_percent:+.1f}%")
        c2.metric("Price flag", flag_badge(rec.price_flag))
        c3.metric("Supplier reliability", f"{rec.reliability_score:.0f}/100")
        if r.price:
            st.caption(r.price.summary)
        if r.reliability:
            st.caption(f"Reliability: {r.reliability.summary}")
    else:
        for c, label in ((c1, "Price change"), (c2, "Price flag"), (c3, "Reliability")):
            c.metric(label, "n/a")
        st.caption("Memory OFF: the agent has no history, so it cannot compare prices or judge reliability.")
    for w in r.warnings:
        st.warning(w)
    st.markdown(f"**Suggested tactic:** `{rec.suggested_tactic.value}`")
    st.markdown("**Negotiation points**")
    for p in rec.negotiation_points:
        st.markdown(f"- {p}")
    with st.expander("Draft counter-offer email", expanded=not compact):
        st.code(rec.draft_email, language=None)  # built-in copy button
    st.caption(f"Generated via: {r.mode}")


def render_memories(r: "agent.AgentResult") -> None:
    st.subheader("🧠 What the agent remembered")
    if not r.memory_enabled:
        st.info("Memory is OFF for this run.")
        return
    if r.reflection:
        st.success(f"**Hindsight reflection:** {r.reflection}")
    if not r.memories:
        st.info("No memories recalled. Load seed data from the sidebar.")
    for m in r.memories:
        st.markdown(f"`{m.get('date') or 'n/a'}` · *{m.get('type') or 'memory'}* — {m['text']}")


def price_chart(supplier_id: str, item: str, new_quote: Quote | None) -> go.Figure:
    quotes = [q for q in ledger.load("quotes") if analysis.same_item(q.item, item)]
    df = analysis.price_trend_df(quotes, NAMES)
    fig = go.Figure()
    for sid, g in df.groupby("supplier_id"):
        sel = sid == supplier_id
        fig.add_trace(go.Scatter(x=g["quote_date"], y=g["unit_price"], mode="lines+markers", name=NAMES[sid],
                                 opacity=1 if sel else 0.35, line=dict(width=4 if sel else 2)))
    qmap = {q.quote_id: q for q in quotes}
    won = [(qmap[o.quote_id].quote_date, o.final_price) for o in ledger.load("outcomes")
           if o.worked and o.supplier_id == supplier_id and o.quote_id in qmap]
    if won:
        fig.add_trace(go.Scatter(x=[w[0] for w in won], y=[w[1] for w in won], mode="markers",
                                 name="Negotiated price (paid)", marker=dict(symbol="diamond", size=11, color="green")))
    if new_quote and new_quote.supplier_id == supplier_id:
        fig.add_trace(go.Scatter(x=[new_quote.quote_date], y=[new_quote.unit_price], mode="markers",
                                 name="New quote", marker=dict(symbol="star", size=18, color="red")))
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10), yaxis_title="Unit price (₹)", legend_orientation="h")
    return fig


# ----------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("🧠 Vendor Memory Agent")
    st.caption("Procurement negotiation agent powered by Hindsight memory")
    st.markdown(f"👤 Signed in as **{ss.user['full_name']}**")
    if os.getenv("AUTH_REQUIRED", "1") != "0" and st.button("Log out"):
        for k in list(ss.keys()):
            del ss[k]
        st.rerun()
    st.selectbox("Supplier", [AUTO] + list(NAMES), key="supplier_choice",
                 format_func=lambda x: x if x == AUTO else f"{NAMES[x]} ({x})")
    st.toggle("Memory ON", value=True, key="memory_on", help="OFF = generic advice (the 'before').")
    st.toggle("Auto-update results", value=True, key="auto_run",
              help="Re-run automatically when the supplier or quote changes.")
    if ss.conn is None:
        ss.conn = store.health()
    ok, msg = ss.conn
    st.markdown(f"**Hindsight:** {'🟢' if ok else '🔴'} {msg}")
    st.markdown(f"**Groq key:** {'🟢 set' if os.getenv('GROQ_API_KEY') else '🔴 missing'}")
    st.markdown(f"**Seed data:** {'🟢 loaded' if load_seed_data.is_loaded() else '⚪ not loaded'}")
    if st.button("Re-check connection"):
        ss.conn = store.health()
        st.rerun()
    if st.button("Load seed data into Hindsight", type="primary"):
        bar = st.status("Loading ~130 memories into Hindsight...", expanded=True)
        try:
            out = load_seed_data.load_seed(force=False, progress=lambda m: bar.write(m))
            bar.update(label="Seed data skipped (already loaded)" if out["skipped"] else "Seed data loaded", state="complete")
        except Exception as e:  # noqa: BLE001
            bar.update(label=f"Load failed: {e}", state="error")
    if st.button("Reset demo (clear logged outcomes)"):
        ledger.reset_runtime()
        ss.sig = ss.compare = ss.last_quote = ss.error = None
        st.toast("Local demo log cleared. (Hindsight memories stay; use a fresh bank to fully reset.)")

# --------------------------------------------------------------- main panel
st.header("Supplier quote analysis")
st.radio("Input", ["Paste message", "Quick form"], horizontal=True, key="input_mode", label_visibility="collapsed")
if ss.input_mode == "Paste message":
    st.selectbox("Load demo quote", ["-"] + [f"Step {d['step']}: {d['title']}" for d in DEMO],
                 key="demo_choice", on_change=load_demo)
    st.text_area("Paste supplier email / WhatsApp message", key="raw_text", height=120,
                 placeholder="Hi sir, new rate for 12x10x8 box Rs 19.40 per pc for 3000 pcs...")
else:
    f1, f2, f3, f4 = st.columns([3, 1, 1, 1])
    f1.selectbox("Item", CANONICAL_SKUS, key="qf_item")
    f2.number_input("Quantity", 1, 1_000_000, 3000, key="qf_qty")
    f3.number_input("Unit price (₹)", 0.01, 100000.0, 19.0, key="qf_price")
    f4.date_input("Quote date", date.today(), key="qf_date")

refresh()  # auto-run if the input changed since the last result

tab_rec, tab_ba, tab_chart, tab_log = st.tabs(["Recommendation", "Before / After", "Price trend", "Log outcome"])


def stale_notice() -> None:
    if ss.error:
        st.error(ss.error)
    elif ss.compare and ss.sig != input_signature():
        st.info("Input changed. Click the button to update (auto-update is off).")


with tab_rec:
    if st.button("Analyze quote", type="primary"):
        refresh(force=True)
    stale_notice()
    if ss.compare:
        r = ss.compare[1] if ss.memory_on else ss.compare[0]   # switching Memory ON/OFF needs no re-run
        st.subheader(f"{r.supplier.name} · ₹{r.quote.unit_price:.2f} × {r.quote.quantity}")
        left, right = st.columns([3, 2])
        with left:
            render_result(r)
        with right:
            render_memories(r)
    elif not ss.error:
        st.info("Paste a supplier message (or load a demo quote). Results appear automatically.")

with tab_ba:
    st.caption("Same quote, same model. Only memory changes. Updates automatically.")
    if st.button("Re-run comparison", type="primary"):
        refresh(force=True)
    stale_notice()
    if ss.compare:
        off, on = ss.compare
        cl, cr = st.columns(2)
        with cl:
            st.markdown("### ❌ Memory OFF (generic)")
            render_result(off, compact=True)
        with cr:
            st.markdown("### ✅ Memory ON (Hindsight)")
            render_result(on, compact=True)
        render_memories(on)

with tab_chart:
    sid = ss.last_quote.supplier_id if ss.last_quote else (
        ss.supplier_choice if ss.get("supplier_choice", AUTO) != AUTO else SUPPLIERS[0].supplier_id)
    item = st.selectbox("Item", CANONICAL_SKUS, key="chart_item")
    st.plotly_chart(price_chart(sid, item, ss.last_quote if ss.last_quote and analysis.same_item(ss.last_quote.item, item) else None),
                    use_container_width=True)
    st.caption(f"Highlighted: {NAMES[sid]}. Star = the new quote, green diamond = price you actually negotiated.")

with tab_log:
    if not ss.last_quote:
        st.info("Analyze a quote first, then log how the negotiation went.")
    else:
        q = ss.last_quote
        st.write(f"Logging for **{NAMES[q.supplier_id]}** quote of ₹{q.unit_price:.2f}")
        with st.form("outcome"):
            tactic = st.selectbox("Tactic used", [t.value for t in Tactic])
            worked = st.radio("Did it work?", ["Yes", "No"], horizontal=True)
            final = st.number_input("Final price (₹/unit)", 0.01, 100000.0, float(q.unit_price))
            notes = st.text_input("Notes (optional)")
            if st.form_submit_button("Save to Hindsight", type="primary"):
                with st.spinner("Retaining outcome..."):
                    by = f"logged by {ss.user['username']}"
                    res = agent.log_outcome(q, Tactic(tactic), worked == "Yes", float(final),
                                            f"{notes} ({by})" if notes else by)
                if res["error"]:
                    st.warning(f"Saved locally, but Hindsight failed: {res['error']}")
                else:
                    st.success(f"Saved {res['retained']} memories to Hindsight. The next quote from this supplier will use them.")
