#!/usr/bin/env python3
"""
Citizenry Retail — Monday Morning Report
Runs every Monday 8am CT via GitHub Actions.
Data: Looker explore API (citizenry model).
Plan: Google Sheet CSV export (must be shared "Anyone with the link can view").
Delivery: Slack incoming webhook → #salesoperations.
"""

import os, sys, datetime, json, csv, io
import requests
from anthropic import Anthropic

# ── Config ────────────────────────────────────────────────────────────────────

LOOKER_URL    = os.environ["LOOKER_BASE_URL"]      # https://havenly.looker.com
LOOKER_ID     = os.environ["LOOKER_CLIENT_ID"]
LOOKER_SECRET = os.environ["LOOKER_CLIENT_SECRET"]

SLACK_WEBHOOK = os.environ["SLACK_WEBHOOK_URL"]    # https://hooks.slack.com/services/...

ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

# Forecast sheet — "For Claude" tab (GID 742042375, always leftmost).
# Sheet must be shared as "Anyone with the link can view".
FORECAST_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1NMb41PXvSsxmj1zeelgARJRE7cEeXYepyfYkQgGy75A"
    "/export?format=csv&gid=742042375"
)

RETAIL_FILTERS = {
    "orders.order_channel": "Retail",
    "orders.order_type":    "Fraud,Purchase,Cancellation",
}

STORE_MAP = {
    "New York - Flagship":                 "soho",
    "Interior Define Studio - Denver CO1": "denver",
    "The Citizenry Dallas TX - TX2":       "dallas",
}

# ── Dates ─────────────────────────────────────────────────────────────────────

def compute_dates():
    today    = datetime.date.today()
    yd       = today - datetime.timedelta(days=1)
    lw_end   = yd
    lw_start = lw_end  - datetime.timedelta(days=6)
    mtd_start = yd.replace(day=1)
    def ly(dt): return dt.replace(year=dt.year - 1)
    return dict(
        today=today, yd=yd,
        lw_start=lw_start, lw_end=lw_end, mtd_start=mtd_start,
        ly_yd=ly(yd),
        ly_lw_start=ly(lw_start), ly_lw_end=ly(lw_end),
        ly_mtd_start=ly(mtd_start),
        week_label=f"Week of {lw_start.strftime('%b %-d')}–{lw_end.strftime('%-d, %Y')}",
    )

# ── Looker ────────────────────────────────────────────────────────────────────

class Looker:
    def __init__(self):
        r = requests.post(f"{LOOKER_URL}/api/4.0/login",
                          data={"client_id": LOOKER_ID, "client_secret": LOOKER_SECRET})
        r.raise_for_status()
        self.h = {"Authorization": f"token {r.json()['access_token']}",
                  "Content-Type": "application/json"}

    def query(self, fields, filters, sorts=None, limit=500):
        body = {"model": "citizenry", "view": "orders",
                "fields": fields, "filters": filters,
                "sorts": sorts or [], "limit": str(limit)}
        r = requests.post(f"{LOOKER_URL}/api/4.0/queries/run/json",
                          headers=self.h, json=body)
        if not r.ok:
            print(f"Looker error {r.status_code}: {r.text[:500]}", file=sys.stderr)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else [data]

    def totals(self, start, end):
        """Revenue, orders, units for a date window — single aggregate row."""
        rows = self.query(
            fields=["orders.cz_actual", "orders.num_orders", "order_items.number_of_items"],
            filters={**RETAIL_FILTERS, "orders.created_date": f"{start} to {end}"},
        )
        row = rows[0] if rows else {}
        return dict(
            revenue=float(row.get("orders.cz_actual", 0) or 0),
            orders=int(row.get("orders.num_orders",   0) or 0),
            units= int(row.get("order_items.number_of_items", 0) or 0),
        )

    def stores(self, start, end):
        """MTD revenue by store."""
        return self.query(
            fields=["orders.store_name", "orders.cz_actual"],
            filters={**RETAIL_FILTERS, "orders.created_date": f"{start} to {end}"},
            sorts=["orders.cz_actual desc"],
        )

    def daily(self, start, end):
        """Day-by-day revenue — returns {day_of_month: revenue}."""
        rows = self.query(
            fields=["orders.created_date", "orders.cz_actual"],
            filters={**RETAIL_FILTERS, "orders.created_date": f"{start} to {end}"},
            sorts=["orders.created_date"],
        )
        return {
            int(str(r.get("orders.created_date", "1900-01-01")).split("-")[2]):
            float(r.get("orders.cz_actual", 0) or 0)
            for r in rows if r.get("orders.created_date")
        }

# ── Google Sheets plan ────────────────────────────────────────────────────────

def get_plan(d):
    """Return {day: {soho, denver, dallas, total}} for current month."""
    try:
        resp = requests.get(FORECAST_CSV_URL, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ⚠  Forecast sheet unavailable: {e}")
        print("     Share the sheet as 'Anyone with the link can view' to enable plan data.")
        return {}

    rows = list(csv.reader(io.StringIO(resp.text)))
    hdr  = next((i for i, r in enumerate(rows) if r and r[0].strip() == "Date"), None)
    if hdr is None:
        return {}

    def pv(s):
        try:    return float(str(s).replace("$","").replace(",","").strip() or 0)
        except: return 0.0

    cm, cy = d["mtd_start"].month, d["mtd_start"].year
    plan   = {}
    for row in rows[hdr + 1:]:
        if not row or not row[0].strip(): continue
        try:
            m, day, y = (int(x) for x in row[0].strip().split("/"))
            if m != cm or y != cy: continue
        except: continue
        s, dv, da = pv(row[1] if len(row)>1 else 0), pv(row[2] if len(row)>2 else 0), pv(row[3] if len(row)>3 else 0)
        plan[day] = {"soho": s, "denver": dv, "dallas": da, "total": s+dv+da}
    return plan

# ── Helpers ───────────────────────────────────────────────────────────────────

def pct(a, b):     return round((a/b - 1)*100, 1) if b else None
def fmtd(v):       return f"${v:,.0f}" if v else "$0"
def arrow(v):      return "▲" if v is not None and v >= 0 else "▼"
def pct_s(v):      return f"{abs(v):.0f}%" if v is not None else "n/a"
def sign(v):       return f"{arrow(v)} {pct_s(v)}" if v is not None else "–"
def fmt_pct(v):    return ("+" if v is not None and v >= 0 else "") + pct_s(v) if v is not None else "n/a"

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    d = compute_dates()
    print(f"Week: {d['week_label']}  yd={d['yd']}  mtd_start={d['mtd_start']}")

    # Plan from Google Sheet
    print("Reading forecast sheet...")
    plan    = get_plan(d)
    yd_day  = d["yd"].day

    def sp(s, e, k="total"): return sum(plan.get(day,{}).get(k,0) for day in range(s,e+1))
    yd_plan  = plan.get(yd_day, {"soho":0,"denver":0,"dallas":0,"total":0})
    lw_plan  = {k: sp(d["lw_start"].day, d["lw_end"].day, k) for k in ("soho","denver","dallas","total")}
    mtd_plan = {k: sp(1, yd_day, k) for k in ("soho","denver","dallas","total")}

    # Looker queries
    print("Connecting to Looker...")
    lk = Looker()

    print("Querying revenue/orders/units (6 windows)...")
    ty_yd  = lk.totals(d["yd"],        d["yd"])
    ly_yd  = lk.totals(d["ly_yd"],     d["ly_yd"])
    ty_lw  = lk.totals(d["lw_start"],  d["lw_end"])
    ly_lw  = lk.totals(d["ly_lw_start"], d["ly_lw_end"])
    ty_mtd = lk.totals(d["mtd_start"], d["yd"])
    ly_mtd = lk.totals(d["ly_mtd_start"], d["ly_yd"])

    print("Querying store breakdown...")
    store_rows = lk.stores(d["mtd_start"], d["yd"])

    print("Querying daily actuals...")
    daily_actuals = lk.daily(d["mtd_start"], d["yd"])

    # ── Compute metrics ───────────────────────────────────────────────────────

    def aov(r):  return round(r["revenue"]/r["orders"], 2) if r["orders"] else 0
    def upt(r):  return round(r["units"]/r["orders"],   2) if r["orders"] else 0

    # vs LY
    yd_rev_vly  = pct(ty_yd["revenue"],  ly_yd["revenue"])
    lw_rev_vly  = pct(ty_lw["revenue"],  ly_lw["revenue"])
    mtd_rev_vly = pct(ty_mtd["revenue"], ly_mtd["revenue"])
    lw_ord_vly  = pct(ty_lw["orders"],   ly_lw["orders"])
    mtd_ord_vly = pct(ty_mtd["orders"],  ly_mtd["orders"])
    lw_aov_vly  = pct(aov(ty_lw),        aov(ly_lw))
    mtd_aov_vly = pct(aov(ty_mtd),       aov(ly_mtd))
    lw_upt_vly  = pct(upt(ty_lw),        upt(ly_lw))
    mtd_upt_vly = pct(upt(ty_mtd),       upt(ly_mtd))

    # vs plan
    yd_vp  = pct(ty_yd["revenue"],  yd_plan["total"])
    lw_vp  = pct(ty_lw["revenue"],  lw_plan["total"])
    mtd_vp = pct(ty_mtd["revenue"], mtd_plan["total"])

    # Stores
    stores = {"soho": 0.0, "denver": 0.0, "dallas": 0.0}
    for row in store_rows:
        key = STORE_MAP.get(row.get("orders.store_name",""))
        if key: stores[key] = float(row.get("orders.cz_actual", 0) or 0)

    soho_vp   = pct(stores["soho"],   mtd_plan.get("soho",   0))
    denver_vp = pct(stores["denver"], mtd_plan.get("denver", 0))
    dallas_vp = pct(stores["dallas"], mtd_plan.get("dallas", 0))

    # ── Narrative ─────────────────────────────────────────────────────────────

    print("Generating narrative...")
    ctx = (
        f"Citizenry Retail — {d['week_label']}\n\n"
        f"MTD Revenue: {fmtd(ty_mtd['revenue'])} | {sign(mtd_rev_vly)} vs LY | {fmt_pct(mtd_vp)} vs plan\n"
        f"MTD Orders: {ty_mtd['orders']} ({sign(mtd_ord_vly)} vs LY) | "
        f"AOV: {fmtd(aov(ty_mtd))} ({sign(mtd_aov_vly)} vs LY) | "
        f"UPT: {upt(ty_mtd)} ({sign(mtd_upt_vly)} vs LY)\n"
        f"Last Week: {fmtd(ty_lw['revenue'])} | {sign(lw_rev_vly)} vs LY | {fmt_pct(lw_vp)} vs plan\n"
        f"Yesterday: {fmtd(ty_yd['revenue'])} | {sign(yd_rev_vly)} vs LY | {fmt_pct(yd_vp)} vs plan\n\n"
        f"Store MTD: SoHo {fmtd(stores['soho'])} ({fmt_pct(soho_vp)} vs plan) | "
        f"Denver {fmtd(stores['denver'])} ({fmt_pct(denver_vp)} vs plan) | "
        f"Dallas {fmtd(stores['dallas'])} ({fmt_pct(dallas_vp)} vs plan)"
    )
    narrative = Anthropic(api_key=ANTHROPIC_KEY).messages.create(
        model="claude-sonnet-4-6", max_tokens=350,
        messages=[{"role": "user", "content":
            "Write a 2-paragraph retail performance summary for the Citizenry exec team. "
            "Direct and factual, 3–4 sentences each. P1: overall MTD vs plan and LY, key driver. "
            "P2: store highlights. Plain text only.\n\n" + ctx}],
    ).content[0].text.strip()

    # ── Slack ─────────────────────────────────────────────────────────────────

    lw_label = f"{d['lw_start'].strftime('%b %-d')}–{d['lw_end'].strftime('%-d')}"

    text = (
        f"📊 *Citizenry Retail — {d['week_label']}*\n\n"

        f"*MTD (thru {d['yd'].strftime('%b %-d')})*\n"
        f"Revenue: *{fmtd(ty_mtd['revenue'])}*  {sign(mtd_rev_vly)} vs LY  |  {fmt_pct(mtd_vp)} vs plan\n"
        f"Orders: *{ty_mtd['orders']}*  {sign(mtd_ord_vly)} vs LY  |  "
        f"AOV: *{fmtd(aov(ty_mtd))}*  {sign(mtd_aov_vly)} vs LY  |  "
        f"UPT: *{upt(ty_mtd)}*  {sign(mtd_upt_vly)} vs LY\n\n"

        f"*Store Breakdown (MTD)*\n"
        f"• SoHo:   {fmtd(stores['soho'])}  ({fmt_pct(soho_vp)} vs plan)\n"
        f"• Denver: {fmtd(stores['denver'])}  ({fmt_pct(denver_vp)} vs plan)\n"
        f"• Dallas: {fmtd(stores['dallas'])}  ({fmt_pct(dallas_vp)} vs plan)\n\n"

        f"*Last Week ({lw_label})*\n"
        f"Revenue: {fmtd(ty_lw['revenue'])}  {sign(lw_rev_vly)} vs LY  |  {fmt_pct(lw_vp)} vs plan\n"
        f"Orders: {ty_lw['orders']}  {sign(lw_ord_vly)} vs LY  |  "
        f"AOV: {fmtd(aov(ty_lw))}  {sign(lw_aov_vly)} vs LY  |  "
        f"UPT: {upt(ty_lw)}  {sign(lw_upt_vly)} vs LY\n\n"

        f"*Yesterday ({d['yd'].strftime('%a %b %-d')})*\n"
        f"{fmtd(ty_yd['revenue'])}  {sign(yd_rev_vly)} vs LY  |  {fmt_pct(yd_vp)} vs plan  |  {ty_yd['orders']} orders\n\n"

        f"_{narrative}_"
    )

    print("Posting to Slack...")
    resp = requests.post(SLACK_WEBHOOK, json={"text": text, "mrkdwn": True})
    if resp.status_code == 200 and resp.text == "ok":
        print("✅  Posted to Slack")
    else:
        print(f"❌  Slack error: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
