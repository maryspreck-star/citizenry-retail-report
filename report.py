#!/usr/bin/env python3
"""
Citizenry Retail — Monday Morning Report
Runs every Monday 8am CT via GitHub Actions.
Data: Looker explore API (citizenry model).
Plan: Google Sheet CSV export (must be shared "Anyone with the link can view").
Delivery: HTML report → GitHub Pages; Slack link → #salesoperations.
"""

import os, sys, datetime, csv, io, base64
import requests

# ── Config ────────────────────────────────────────────────────────────────────

LOOKER_URL    = os.environ["LOOKER_BASE_URL"]
LOOKER_ID     = os.environ["LOOKER_CLIENT_ID"]
LOOKER_SECRET = os.environ["LOOKER_CLIENT_SECRET"]

SLACK_WEBHOOK  = os.environ["SLACK_WEBHOOK_URL"]
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO    = "maryspreck-star/citizenry-retail-report"
PAGE_URL       = "https://maryspreck-star.github.io/citizenry-retail-report/"

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

STORE_LABELS = {"soho": "SoHo", "denver": "Denver", "dallas": "Dallas"}

# ── Dates ─────────────────────────────────────────────────────────────────────

def compute_dates():
    today     = datetime.date.today()
    yd        = today - datetime.timedelta(days=1)
    yd_minus1 = yd - datetime.timedelta(days=1)
    lw_end    = yd
    lw_start  = lw_end - datetime.timedelta(days=6)
    mtd_start = yd.replace(day=1)
    def ly(dt): return dt.replace(year=dt.year - 1)
    return dict(
        today=today, yd=yd, yd_minus1=yd_minus1,
        lw_start=lw_start, lw_end=lw_end, mtd_start=mtd_start,
        ly_yd=ly(yd), ly_yd_minus1=ly(yd_minus1),
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
            print(f"Looker error {r.status_code}: {r.text[:300]}", file=sys.stderr)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else [data]

    def _date_filter(self, start, end):
        return str(start) if start == end else f"{start} to {end}"

    def totals(self, start, end):
        rows = self.query(
            fields=["orders.cz_actual", "orders.num_orders", "order_items.number_of_items"],
            filters={**RETAIL_FILTERS, "orders.created_date": self._date_filter(start, end)},
        )
        row = rows[0] if rows else {}
        return dict(
            revenue=float(row.get("orders.cz_actual", 0) or 0),
            orders=int(row.get("orders.num_orders", 0) or 0),
            units=int(row.get("order_items.number_of_items", 0) or 0),
        )

    def stores(self, start, end):
        rows = self.query(
            fields=["orders.store_name", "orders.cz_actual", "orders.num_orders"],
            filters={**RETAIL_FILTERS, "orders.created_date": self._date_filter(start, end)},
            sorts=["orders.cz_actual desc"],
        )
        result = {"soho": 0.0, "denver": 0.0, "dallas": 0.0}
        for row in rows:
            k = STORE_MAP.get(row.get("orders.store_name", ""))
            if k:
                result[k] = float(row.get("orders.cz_actual", 0) or 0)
        return result

    def daily(self, start, end):
        rows = self.query(
            fields=["orders.created_date", "orders.cz_actual"],
            filters={**RETAIL_FILTERS, "orders.created_date": self._date_filter(start, end)},
            sorts=["orders.created_date"],
        )
        return {
            int(str(r.get("orders.created_date", "1900-01-01")).split("-")[2]):
            float(r.get("orders.cz_actual", 0) or 0)
            for r in rows if r.get("orders.created_date")
        }

    def monthly_trend(self, start, end):
        try:
            rows = self.query(
                fields=["orders.created_month", "orders.cz_actual", "orders.num_orders",
                        "order_items.number_of_items"],
                filters={**RETAIL_FILTERS, "orders.created_date": self._date_filter(start, end)},
                sorts=["orders.created_month desc"],
                limit=48,
            )
            return rows
        except Exception as e:
            print(f"  ⚠  Monthly trend query failed: {e}")
            return []

    def audience(self, start, end):
        rows = self.query(
            fields=["customers.customer_group", "orders.cz_actual", "orders.num_orders"],
            filters={**RETAIL_FILTERS, "orders.created_date": self._date_filter(start, end)},
        )
        result = {"B2C": {"revenue": 0.0, "orders": 0}, "Trade": {"revenue": 0.0, "orders": 0}}
        for row in rows:
            grp = row.get("customers.customer_group") or "B2C"
            if grp in result:
                result[grp]["revenue"] += float(row.get("orders.cz_actual", 0) or 0)
                result[grp]["orders"]  += int(row.get("orders.num_orders", 0) or 0)
        return result

    def audience_stores(self, start, end):
        rows = self.query(
            fields=["orders.store_name", "customers.customer_group", "orders.cz_actual"],
            filters={**RETAIL_FILTERS, "orders.created_date": self._date_filter(start, end)},
        )
        result = {k: {"B2C": 0.0, "Trade": 0.0} for k in STORE_MAP.values()}
        for row in rows:
            sk  = STORE_MAP.get(row.get("orders.store_name", ""))
            grp = row.get("customers.customer_group") or "B2C"
            if sk and grp in ("B2C", "Trade"):
                result[sk][grp] += float(row.get("orders.cz_actual", 0) or 0)
        return result

    def categories(self, start, end):
        try:
            rows = self.query(
                fields=["products.product_type", "orders.cz_actual",
                        "order_items.number_of_items"],
                filters={**RETAIL_FILTERS, "orders.created_date": self._date_filter(start, end)},
                sorts=["orders.cz_actual desc"],
                limit=20,
            )
            if rows and not rows[0].get("products.product_type"):
                return []
            return rows
        except Exception as e:
            print(f"  ⚠  Category query failed: {e}")
            return []

# ── Google Sheets plan ────────────────────────────────────────────────────────

def get_plan(d):
    try:
        resp = requests.get(FORECAST_CSV_URL, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ⚠  Forecast sheet unavailable: {e}")
        return {}

    rows = list(csv.reader(io.StringIO(resp.text)))
    hdr  = next((i for i, r in enumerate(rows) if r and r[0].strip() == "Date"), None)
    if hdr is None:
        return {}

    def pv(s):
        try:    return float(str(s).replace("$", "").replace(",", "").strip() or 0)
        except: return 0.0

    cm, cy = d["mtd_start"].month, d["mtd_start"].year
    plan   = {}
    for row in rows[hdr + 1:]:
        if not row or not row[0].strip(): continue
        try:
            m, day, y = (int(x) for x in row[0].strip().split("/"))
            if m != cm or y != cy: continue
        except: continue
        s  = pv(row[1] if len(row) > 1 else 0)
        dv = pv(row[2] if len(row) > 2 else 0)
        da = pv(row[3] if len(row) > 3 else 0)
        plan[day] = {"soho": s, "denver": dv, "dallas": da, "total": s + dv + da}
    return plan

# ── Helpers ───────────────────────────────────────────────────────────────────

def pct(a, b):   return round((a / b - 1) * 100, 1) if b else None
def fmtd(v):     return f"${v:,.0f}" if v else "$0"
def arrow(v):    return "▲" if v is not None and v >= 0 else "▼"
def pct_s(v):    return f"{abs(v):.0f}%" if v is not None else "n/a"
def sign(v):     return f"{arrow(v)} {pct_s(v)}" if v is not None else "–"
def fmt_pct(v):  return (("+" if v >= 0 else "−") + pct_s(v)) if v is not None else "–"
def css_cls(v):  return "pos" if v is not None and v >= 0 else "neg"

def aov_fn(r):   return round(r["revenue"] / r["orders"], 0) if r["orders"] else 0
def upt_fn(r):   return round(r["units"]   / r["orders"], 2) if r["orders"] else 0

# ── HTML ──────────────────────────────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #F7F4F0; color: #2C2A27; font-size: 13px; line-height: 1.5; }
.header { background: #1C1A17; color: #F7F4F0; padding: 20px 28px;
          display: flex; justify-content: space-between; align-items: flex-end; }
.header-title { font-size: 22px; font-weight: 600; letter-spacing: -0.3px; }
.header-sub { font-size: 12px; color: #9A9088; margin-top: 3px; }
.header-right { text-align: right; font-size: 12px; color: #9A9088; line-height: 1.7; }
.content { padding: 20px 28px; max-width: 1060px; }
.perf-summary { background: white; border: 1px solid #E4DFDA; border-radius: 6px;
                padding: 16px 20px; font-size: 13px; line-height: 1.7; color: #3A3630; }
.perf-summary p { margin-bottom: 10px; }
.perf-summary p:last-child { margin-bottom: 0; }
.section-label { font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 1.2px; color: #8A8278; margin: 22px 0 10px;
  border-bottom: 1px solid #E4DFDA; padding-bottom: 6px; }
.period-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }
.card { background: white; border: 1px solid #E4DFDA; border-radius: 6px; padding: 14px 16px; }
.card-period { font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 1px; color: #8A8278; margin-bottom: 12px; }
.kpi { display: flex; justify-content: space-between; align-items: baseline;
       padding: 7px 0; border-bottom: 1px solid #F2EFE9; }
.kpi:last-of-type { border-bottom: none; }
.kpi-name { font-size: 12px; color: #6A645E; }
.kpi-right { text-align: right; }
.kpi-val { font-size: 14px; font-weight: 600; }
.kpi-delta { font-size: 11px; color: #8A8278; margin-top: 1px; }
.pos { color: #2D6A4F; } .neg { color: #B85250; } .neutral { color: #8A8278; }
.plan-section { margin-top: 12px; padding-top: 12px; border-top: 1px solid #F2EFE9; }
.plan-header { display: flex; justify-content: space-between;
               font-size: 11px; color: #6A645E; margin-bottom: 6px; }
.plan-bar-track { background: #F0EDE8; border-radius: 3px; height: 7px; overflow: hidden; }
.plan-bar-fill { background: #5B4E3C; height: 100%; border-radius: 3px; }
.plan-bar-fill.over { background: #2D6A4F; }
.plan-detail { font-size: 11px; color: #8A8278; margin-top: 5px; text-align: right; }
.tbl { width: 100%; border-collapse: collapse; background: white;
       border: 1px solid #E4DFDA; border-radius: 6px; overflow: hidden; }
.tbl th { background: #F0EDE8; font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.8px; color: #6A645E; padding: 8px 12px; text-align: right; white-space: nowrap; }
.tbl th:first-child { text-align: left; }
.tbl td { padding: 9px 12px; text-align: right; border-bottom: 1px solid #F4F1ED;
          font-size: 12px; white-space: nowrap; }
.tbl td:first-child { text-align: left; }
.tbl tr:last-child td { border-bottom: none; }
.tbl .total-row td { font-weight: 700; background: #FAFAF8; }
.col-divider { border-left: 2px solid #EDE8E2 !important; }
.store-name { font-weight: 600; }
.store-tag { font-size: 10px; color: #9A9088; font-weight: 400; margin-left: 4px; }
.chart-wrap { background: white; border: 1px solid #E4DFDA; border-radius: 6px; padding: 16px; }
.chart-legend { display: flex; gap: 18px; margin-bottom: 14px;
                font-size: 11px; color: #6A645E; }
.dot { width: 10px; height: 10px; border-radius: 2px; display: inline-block;
       margin-right: 5px; vertical-align: middle; }
.cat-tbl { width: 100%; border-collapse: collapse; background: white;
           border: 1px solid #E4DFDA; border-radius: 6px; overflow: hidden; }
.cat-tbl th { background: #F0EDE8; font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.8px; color: #6A645E; padding: 8px 12px; text-align: right; }
.cat-tbl th:first-child { text-align: left; }
.cat-tbl td { padding: 8px 12px; text-align: right;
              border-bottom: 1px solid #F4F1ED; font-size: 12px; }
.cat-tbl td:first-child { text-align: left; }
.cat-tbl tr:last-child td { border-bottom: none; }
.mix-bar { display: inline-block; height: 6px; background: #D4C5B0;
           border-radius: 2px; vertical-align: middle; margin-right: 6px; }
.trend-tbl { width: 100%; border-collapse: collapse; background: white;
             border: 1px solid #E4DFDA; border-radius: 6px; overflow: hidden; }
.trend-tbl th { background: #F0EDE8; font-size: 10px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.8px; color: #6A645E;
  padding: 7px 12px; text-align: right; }
.trend-tbl th:first-child { text-align: left; }
.trend-tbl td { padding: 7px 12px; text-align: right;
  border-bottom: 1px solid #F4F1ED; font-size: 12px; }
.trend-tbl td:first-child { text-align: left; font-weight: 500; }
.trend-tbl tr:last-child td { border-bottom: none; }
.trend-tbl .partial td { color: #6A645E; font-style: italic; }
.footer { padding: 16px 28px; font-size: 11px; color: #A8A09A;
          border-top: 1px solid #E4DFDA; margin-top: 8px; }
"""

def make_html(d, ty_yd, ty_lw, ty_mtd, ly_yd, ly_lw, ly_mtd,
              stores_yd, stores_lw, stores_mtd,
              stores_ly_yd, stores_ly_lw, stores_ly_mtd,
              plan, daily_actuals, categories, monthly_trend,
              ly_categories=None,
              aud_yd=None, aud_lw=None, aud_mtd=None, aud_mtd_stores=None):

    def sp(s, e, k="total"):
        return sum(plan.get(day, {}).get(k, 0) for day in range(s, e + 1))

    yd_day  = d["yd"].day
    yd_plan = plan.get(yd_day, {}).get("total", 0)
    lw_plan = sp(d["lw_start"].day, d["lw_end"].day)
    mtd_plan = sp(1, yd_day)

    # ── ly_rev_map for category vs LY ────────────────────────────────────────
    ly_rev_map = {}
    if ly_categories:
        for r in ly_categories:
            cat = r.get("products.product_type") or ""
            if cat:
                ly_rev_map[cat] = float(r.get("orders.cz_actual", 0) or 0)

    # ── Performance summary paragraphs ───────────────────────────────────────
    mtd_vly_v = pct(ty_mtd["revenue"], ly_mtd["revenue"])
    mtd_vp_v  = pct(ty_mtd["revenue"], mtd_plan)
    lw_vly_v  = pct(ty_lw["revenue"],  ly_lw["revenue"])
    lw_vp_v   = pct(ty_lw["revenue"],  lw_plan)
    yd_vp_v   = pct(ty_yd["revenue"],  yd_plan)
    yd_vly_v  = pct(ty_yd["revenue"],  ly_yd["revenue"])

    def _s(v): return (f"+{abs(v):.0f}%" if v >= 0 else f"−{abs(v):.0f}%") if v is not None else "n/a"

    # Para 1: MTD + last week + yesterday
    p1 = (f"MTD retail revenue of {fmtd(ty_mtd['revenue'])} is tracking "
          f"{_s(mtd_vp_v)} vs plan and {sign(mtd_vly_v)} vs last year "
          f"through {d['yd'].strftime('%B %-d')}.")
    if ty_lw["revenue"] > 0:
        lw_note = "ahead of plan" if (lw_vp_v or 0) >= 0 else f"{_s(lw_vp_v)} vs plan"
        p1 += (f" Last week ({d['lw_start'].strftime('%b %-d')}–{d['lw_end'].strftime('%-d')}) "
               f"came in at {fmtd(ty_lw['revenue'])}, {lw_note} and {sign(lw_vly_v)} vs LY "
               f"({ty_lw['orders']} orders, AOV {fmtd(aov_fn(ty_lw))}).")
    if ty_yd["revenue"] == 0 and yd_plan > 0:
        p1 += (f" No orders were recorded {d['yd'].strftime('%A, %B %-d')}, "
               f"driving the full gap against a {fmtd(yd_plan)} daily plan.")
    elif ty_yd["revenue"] > 0:
        p1 += (f" Yesterday ({d['yd'].strftime('%a %b %-d')}): "
               f"{fmtd(ty_yd['revenue'])} — {sign(yd_vly_v)} vs LY, {_s(yd_vp_v)} vs plan, "
               f"{ty_yd['orders']} order{'s' if ty_yd['orders'] != 1 else ''}.")

    # Para 2: Store breakdown with insights
    def _store_line(key):
        rev  = stores_mtd.get(key, 0)
        ly_r = stores_ly_mtd.get(key, 0)
        vly  = pct(rev, ly_r)
        vp_s = pct(rev, sp(1, yd_day, key))
        bits = [fmtd(rev)]
        if vp_s is not None: bits.append(f"{_s(vp_s)} vs plan")
        if vly  is not None: bits.append(f"{sign(vly)} vs LY")
        return f"({', '.join(bits)})"

    soho_vp   = pct(stores_mtd.get("soho",   0), sp(1, yd_day, "soho"))
    denver_vp = pct(stores_mtd.get("denver", 0), sp(1, yd_day, "denver"))
    dallas_vp = pct(stores_mtd.get("dallas", 0), sp(1, yd_day, "dallas"))

    p2 = (f"SoHo leads MTD at {fmtd(stores_mtd.get('soho', 0))} {_store_line('soho')}. "
          f"Denver at {fmtd(stores_mtd.get('denver', 0))} {_store_line('denver')}")
    if denver_vp is not None and denver_vp > 0:
        p2 += " — the strongest relative performer vs plan this month"
    p2 += f". Dallas at {fmtd(stores_mtd.get('dallas', 0))} {_store_line('dallas')}"
    if dallas_vp is not None and dallas_vp < -40:
        p2 += ", continues to ramp as a newer location"
    p2 += "."

    # Para 3: Category highlights with vs LY
    p3 = ""
    if categories and ty_mtd["revenue"] > 0:
        total_cat = sum(float(r.get("orders.cz_actual", 0) or 0) for r in categories)
        insights = []
        for r in categories:
            cat  = r.get("products.product_type", "") or ""
            if not cat: continue
            crev = float(r.get("orders.cz_actual", 0) or 0)
            mix  = round(crev / total_cat * 100) if total_cat else 0
            vly  = pct(crev, ly_rev_map[cat]) if cat in ly_rev_map else None
            insights.append((cat, crev, mix, vly))

        top3_parts = []
        for cat, crev, mix, vly in insights[:3]:
            part = f"{cat} {fmtd(crev)} ({mix}%"
            if vly is not None: part += f", {sign(vly)} vs LY"
            part += ")"
            top3_parts.append(part)
        p3 = "Top categories MTD: " + ", ".join(top3_parts) + "."

        gaps = [(c, v) for c, _, m, v in insights if v is not None and v < -40 and m >= 3]
        if gaps:
            gap_str = ", ".join(f"{c} ({sign(v)} vs LY)" for c, v in gaps[:3])
            p3 += f" Notable gaps vs LY: {gap_str}."

    # Para 4: Audience (Trade vs B2C)
    p4 = ""
    if aud_mtd:
        trade_rev = aud_mtd.get("Trade", {}).get("revenue", 0)
        b2c_rev   = aud_mtd.get("B2C",   {}).get("revenue", 0)
        total_aud = trade_rev + b2c_rev
        if total_aud > 0:
            trade_pct = round(trade_rev / total_aud * 100)
            p4 = (f"Trade accounted for {trade_pct}% of MTD revenue ({fmtd(trade_rev)}), "
                  f"with B2C at {100 - trade_pct}% ({fmtd(b2c_rev)}).")
            if aud_lw:
                lw_trade = aud_lw.get("Trade", {}).get("revenue", 0)
                lw_b2c   = aud_lw.get("B2C",   {}).get("revenue", 0)
                lw_tot   = lw_trade + lw_b2c
                if lw_tot > 0:
                    lw_trade_pct = round(lw_trade / lw_tot * 100)
                    p4 += (f" Last week Trade was {lw_trade_pct}% of weekly revenue "
                           f"({fmtd(lw_trade)}).")

    summary_html = (
        '<div class="section-label">Performance Summary</div>'
        '<div class="perf-summary">'
        f'<p>{p1}</p>'
        f'<p>{p2}</p>'
        + (f'<p>{p3}</p>' if p3 else '')
        + (f'<p>{p4}</p>' if p4 else '')
        + '</div>'
    )

    def kpi_card(period_label, r, ly_r, plan_total, plan_label, aud=None):
        rev_vly = pct(r["revenue"], ly_r["revenue"])
        ord_vly = pct(r["orders"],  ly_r["orders"])
        aov_vly = pct(aov_fn(r),    aov_fn(ly_r))
        upt_vly = pct(upt_fn(r),    upt_fn(ly_r))
        vp      = pct(r["revenue"], plan_total)
        bar_w   = min(100, round(r["revenue"] / plan_total * 100)) if plan_total else 0
        over    = " over" if vp is not None and vp >= 0 else ""
        upt_row = "" if r["orders"] == 0 else f"""
      <div class="kpi">
        <span class="kpi-name">UPT</span>
        <div class="kpi-right">
          <div class="kpi-val">{upt_fn(r)}</div>
          <div class="kpi-delta"><span class="{css_cls(upt_vly)}">{sign(upt_vly)} vs LY</span></div>
        </div>
      </div>"""
        aud_row = ""
        if aud:
            trade_rev = aud.get("Trade", {}).get("revenue", 0)
            b2c_rev   = aud.get("B2C",   {}).get("revenue", 0)
            total_aud = trade_rev + b2c_rev or 1
            trade_pct = round(trade_rev / total_aud * 100)
            b2c_pct   = 100 - trade_pct
            aud_row = f"""
      <div class="kpi">
        <span class="kpi-name">Audience</span>
        <div class="kpi-right" style="font-size:11px;line-height:1.6;">
          <div>Trade &nbsp;<strong>{fmtd(trade_rev)}</strong> <span style="color:#8A8278;">({trade_pct}%)</span></div>
          <div>B2C &nbsp;<strong>{fmtd(b2c_rev)}</strong> <span style="color:#8A8278;">({b2c_pct}%)</span></div>
        </div>
      </div>"""
        return f"""
    <div class="card">
      <div class="card-period">{period_label}</div>
      <div class="kpi">
        <span class="kpi-name">Revenue</span>
        <div class="kpi-right">
          <div class="kpi-val">{fmtd(r["revenue"])}</div>
          <div class="kpi-delta"><span class="{css_cls(rev_vly)}">{sign(rev_vly)} vs LY</span></div>
        </div>
      </div>
      <div class="kpi">
        <span class="kpi-name">Orders</span>
        <div class="kpi-right">
          <div class="kpi-val">{r["orders"]}</div>
          <div class="kpi-delta"><span class="{css_cls(ord_vly)}">{sign(ord_vly)} vs LY</span></div>
        </div>
      </div>
      <div class="kpi">
        <span class="kpi-name">AOV</span>
        <div class="kpi-right">
          <div class="kpi-val">{"n/a" if not r["orders"] else fmtd(aov_fn(r))}</div>
          <div class="kpi-delta"><span class="{css_cls(aov_vly)}">{sign(aov_vly)} vs LY</span></div>
        </div>
      </div>{upt_row}{aud_row}
      <div class="plan-section">
        <div class="plan-header">
          <span>vs Plan ({plan_label})</span>
          <strong class="{css_cls(vp)}">{fmt_pct(vp)}</strong>
        </div>
        <div class="plan-bar-track">
          <div class="plan-bar-fill{over}" style="width:{bar_w}%;"></div>
        </div>
        <div class="plan-detail">{fmtd(r["revenue"])} / {fmtd(plan_total)} plan</div>
      </div>
    </div>"""

    # Store table
    def store_tbl_row(key, label, tag=""):
        tag_html = f'<span class="store-tag">{tag}</span>' if tag else ""
        yd_vly  = pct(stores_yd.get(key, 0),  stores_ly_yd.get(key, 0))
        lw_vly  = pct(stores_lw.get(key, 0),  stores_ly_lw.get(key, 0))
        mtd_vly = pct(stores_mtd.get(key, 0), stores_ly_mtd.get(key, 0))
        mtd_plan_store = sp(1, yd_day, key)
        mtd_vp  = pct(stores_mtd.get(key, 0), mtd_plan_store)
        return (f'<tr><td><span class="store-name">{label}</span>{tag_html}</td>'
                f'<td>{fmtd(stores_yd.get(key,0))}</td>'
                f'<td><span class="{css_cls(yd_vly)}">{sign(yd_vly)}</span></td>'
                f'<td class="col-divider">{fmtd(stores_lw.get(key,0))}</td>'
                f'<td><span class="{css_cls(lw_vly)}">{sign(lw_vly)}</span></td>'
                f'<td class="col-divider">{fmtd(stores_mtd.get(key,0))}</td>'
                f'<td><span class="{css_cls(mtd_vp)}">{fmt_pct(mtd_vp)}</span></td>'
                f'<td><span class="{css_cls(mtd_vly)}">{sign(mtd_vly)}</span></td></tr>')

    total_lw_vly  = pct(ty_lw["revenue"],  ly_lw["revenue"])
    total_mtd_vly = pct(ty_mtd["revenue"], ly_mtd["revenue"])
    total_mtd_vp  = pct(ty_mtd["revenue"], mtd_plan)

    store_table = f"""
  <table class="tbl">
    <thead>
      <tr>
        <th>Store</th>
        <th>Yesterday</th><th>vs LY</th>
        <th class="col-divider">Last Week ({d['lw_start'].strftime('%b %-d')}–{d['lw_end'].strftime('%-d')})</th><th>vs LY</th>
        <th class="col-divider">MTD</th><th>vs Plan</th><th>vs LY</th>
      </tr>
    </thead>
    <tbody>
      <tr class="total-row">
        <td>Total Retail</td>
        <td>{fmtd(ty_yd["revenue"])}</td>
        <td><span class="{css_cls(pct(ty_yd['revenue'], ly_yd['revenue']))}">{sign(pct(ty_yd['revenue'], ly_yd['revenue']))}</span></td>
        <td class="col-divider">{fmtd(ty_lw["revenue"])}</td>
        <td><span class="{css_cls(total_lw_vly)}">{sign(total_lw_vly)}</span></td>
        <td class="col-divider">{fmtd(ty_mtd["revenue"])}</td>
        <td><span class="{css_cls(total_mtd_vp)}">{fmt_pct(total_mtd_vp)}</span></td>
        <td><span class="{css_cls(total_mtd_vly)}">{sign(total_mtd_vly)}</span></td>
      </tr>
      {store_tbl_row("soho",   "SoHo",   "flagship")}
      {store_tbl_row("denver", "Denver")}
      {store_tbl_row("dallas", "Dallas")}
    </tbody>
  </table>"""

    # Audience by store MTD table
    aud_store_table = ""
    if aud_mtd_stores and aud_mtd:
        def aud_store_row(key, label):
            trade = aud_mtd_stores.get(key, {}).get("Trade", 0)
            b2c   = aud_mtd_stores.get(key, {}).get("B2C",   0)
            tot   = trade + b2c or 1
            return (f'<tr><td><span class="store-name">{label}</span></td>'
                    f'<td>{fmtd(trade)}</td><td style="color:#8A8278;">{round(trade/tot*100)}%</td>'
                    f'<td>{fmtd(b2c)}</td><td style="color:#8A8278;">{round(b2c/tot*100)}%</td></tr>')
        tot_trade = aud_mtd.get("Trade", {}).get("revenue", 0)
        tot_b2c   = aud_mtd.get("B2C",   {}).get("revenue", 0)
        tot_all   = tot_trade + tot_b2c or 1
        aud_store_table = f"""
  <div class="section-label">Audience Mix &mdash; MTD {d['mtd_start'].strftime('%b %-d')}–{d['yd'].strftime('%-d')}</div>
  <table class="tbl">
    <thead>
      <tr><th>Store</th><th>Trade Rev</th><th>Trade %</th><th>B2C Rev</th><th>B2C %</th></tr>
    </thead>
    <tbody>
      {aud_store_row('soho',   'SoHo')}
      {aud_store_row('denver', 'Denver')}
      {aud_store_row('dallas', 'Dallas')}
      <tr class="total-row">
        <td>Total</td>
        <td>{fmtd(tot_trade)}</td><td style="color:#8A8278;">{round(tot_trade/tot_all*100)}%</td>
        <td>{fmtd(tot_b2c)}</td><td style="color:#8A8278;">{round(tot_b2c/tot_all*100)}%</td>
      </tr>
    </tbody>
  </table>"""

    # Daily chart data
    days_list  = list(range(1, yd_day + 1))
    actuals_js = ", ".join(str(daily_actuals.get(d_num, 0)) for d_num in days_list)
    plans_js   = ", ".join(str(plan.get(d_num, {}).get("total", 0)) for d_num in days_list)
    labels_js  = ", ".join(f"'{d_num}'" for d_num in days_list)
    max_val    = max([daily_actuals.get(n, 0) for n in days_list] +
                     [plan.get(n, {}).get("total", 0) for n in days_list] + [1])
    chart_max  = max(8000, (int(max_val / 2000) + 1) * 2000)

    # Category table
    cat_html = ""
    if categories:
        total_cat_rev = sum(float(r.get("orders.cz_actual", 0) or 0) for r in categories)
        rows_html = ""
        for r in categories:
            cat   = r.get("products.product_type", "Other") or "Other"
            rev   = float(r.get("orders.cz_actual", 0) or 0)
            units = int(r.get("order_items.number_of_items", 0) or 0)
            mix   = round(rev / total_cat_rev * 100) if total_cat_rev else 0
            bar_w = max(1, round(mix * 2.5))
            cat_vly = pct(rev, ly_rev_map[cat]) if cat in ly_rev_map else None
            vly_cell = (f'<span class="{css_cls(cat_vly)}">{sign(cat_vly)}</span>'
                        if cat_vly is not None else '<span class="neutral">n/a</span>')
            rows_html += (f'<tr><td>{cat}</td><td>{fmtd(rev)}</td>'
                          f'<td style="text-align:left;padding-left:8px;">'
                          f'<span class="mix-bar" style="width:{bar_w}px;"></span>{mix}%</td>'
                          f'<td>{units}</td><td>{vly_cell}</td></tr>')
        cat_html = f"""
  <div class="section-label">Category Mix &mdash; MTD {d['mtd_start'].strftime('%b %-d')}–{d['yd'].strftime('%-d')}</div>
  <table class="cat-tbl">
    <thead>
      <tr><th>Category</th><th>Revenue</th>
      <th style="text-align:left;padding-left:8px;">Mix</th>
      <th>Units</th><th>vs LY Rev</th></tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>"""

    # Monthly trend table
    trend_html = ""
    if monthly_trend:
        month_map = {}
        for row in monthly_trend:
            mo = str(row.get("orders.created_month") or row.get("orders.created_date_month", ""))[:7]
            if not mo: continue
            month_map[mo] = dict(
                revenue=float(row.get("orders.cz_actual", 0) or 0),
                orders=int(row.get("orders.num_orders", 0) or 0),
                units=int(row.get("order_items.number_of_items", 0) or 0),
            )
        rows_html = ""
        cur = d["yd"].replace(day=1)
        for _ in range(18):
            mo_key  = cur.strftime("%Y-%m")
            ly_key  = (cur.replace(year=cur.year - 1)).strftime("%Y-%m")
            data    = month_map.get(mo_key)
            if not data:
                cur = (cur.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
                continue
            ly_data = month_map.get(ly_key)
            rev_vly = pct(data["revenue"], ly_data["revenue"]) if ly_data else None
            aov_m   = round(data["revenue"] / data["orders"]) if data["orders"] else 0
            upt_m   = round(data["units"]   / data["orders"], 1) if data["orders"] else 0
            is_partial = (cur.year == d["yd"].year and cur.month == d["yd"].month)
            label   = cur.strftime("%b %Y") + (" (partial)" if is_partial else "")
            row_cls = ' class="partial"' if is_partial else ""
            vly_html = (f'<span class="{css_cls(rev_vly)}">{sign(rev_vly)}</span>'
                        if rev_vly is not None else '<span class="neutral">n/a</span>')
            rows_html += (f'<tr{row_cls}><td>{label}</td>'
                          f'<td>{fmtd(data["revenue"])}</td>'
                          f'<td>{data["orders"]}</td>'
                          f'<td>{fmtd(aov_m)}</td>'
                          f'<td>{upt_m}</td>'
                          f'<td>{vly_html}</td></tr>')
            cur = (cur.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
        trend_html = f"""
  <div class="section-label">Monthly Performance Trend</div>
  <table class="trend-tbl">
    <thead>
      <tr><th>Month</th><th>Revenue</th><th>Orders</th><th>AOV</th><th>UPT</th><th>vs LY</th></tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>"""

    lw_label  = f"{d['lw_start'].strftime('%b %-d')}–{d['lw_end'].strftime('%-d, %Y')}"
    yd_label  = d["yd"].strftime("%a %b %-d")
    mtd_label = f"{d['mtd_start'].strftime('%b %-d')}–{d['yd'].strftime('%-d')}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Citizenry Retail — {d['week_label']}</title>
<style>{CSS}</style>
</head>
<body>

<div class="header">
  <div>
    <div class="header-title">Citizenry Retail</div>
    <div class="header-sub">Weekly Report &mdash; {d['week_label']}</div>
  </div>
  <div class="header-right">
    Data through {d['yd'].strftime('%a %b %-d, %Y')}<br>
    SoHo &middot; Denver &middot; Dallas
  </div>
</div>

<div class="content">

  {summary_html}

  <div class="section-label">Performance Overview</div>
  <div class="period-grid">
    {kpi_card(f"Yesterday &mdash; {yd_label}", ty_yd, ly_yd, yd_plan, d['yd'].strftime('%b %-d'), aud_yd)}
    {kpi_card(f"Last Week &mdash; {d['lw_start'].strftime('%b %-d')}–{d['lw_end'].strftime('%-d')}", ty_lw, ly_lw, lw_plan, lw_label, aud_lw)}
    {kpi_card(f"MTD &mdash; {mtd_label}", ty_mtd, ly_mtd, mtd_plan, mtd_label, aud_mtd)}
  </div>

  <div class="section-label">Store Performance</div>
  {store_table}

  {aud_store_table}

  <div class="section-label">MTD Daily Revenue vs Plan &mdash; {d['mtd_start'].strftime('%b %Y')}</div>
  <div class="chart-wrap">
    <div class="chart-legend">
      <span><span class="dot" style="background:#5B4E3C;"></span>Actual Revenue</span>
      <span><span class="dot" style="background:#D4C5B0;"></span>Daily Plan</span>
    </div>
    <canvas id="dailyChart" style="width:100%;height:auto;display:block;"></canvas>
  </div>

  {cat_html}
  {trend_html}

</div>

<div class="footer">
  Generated {d['today'].strftime('%a %b %-d, %Y')} &nbsp;&middot;&nbsp;
  Data through {d['yd'].strftime('%b %-d, %Y')} &nbsp;&middot;&nbsp;
  Source: Looker / Citizenry model &nbsp;&middot;&nbsp;
  Filter: Retail channel
</div>

<script>
(function() {{
  var canvas = document.getElementById('dailyChart');
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var days    = [{labels_js}];
  var actuals = [{actuals_js}];
  var plans   = [{plans_js}];
  var W = 900, H = 200;
  canvas.width = W; canvas.height = H;
  var padL=56, padR=12, padT=8, padB=28;
  var cW=W-padL-padR, cH=H-padT-padB;
  var MAX={chart_max}, n=days.length;
  var groupW=cW/n, bW=Math.max(groupW*0.3, 5);
  var step=MAX/4;
  [0, step, step*2, step*3, MAX].forEach(function(v) {{
    var y=padT+cH-(v/MAX)*cH;
    ctx.save(); ctx.strokeStyle='#EDE8E2'; ctx.lineWidth=1; ctx.setLineDash([3,3]);
    ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(padL+cW,y); ctx.stroke(); ctx.restore();
    ctx.fillStyle='#9A9088'; ctx.font='10px -apple-system,sans-serif';
    ctx.textAlign='right';
    ctx.fillText(v===0?'$0':'$'+(v/1000)+'K', padL-5, y+3.5);
  }});
  days.forEach(function(d,i) {{
    var gx=padL+i*groupW, cx=gx+groupW/2;
    var pH=(plans[i]/MAX)*cH;
    if (plans[i]>0) {{ ctx.fillStyle='#D4C5B0'; ctx.fillRect(cx-bW,padT+cH-pH,bW,Math.max(pH,1)); }}
    var aH=(actuals[i]/MAX)*cH;
    ctx.fillStyle=actuals[i]>0?'#5B4E3C':'#F0EDE8';
    ctx.fillRect(cx,padT+cH-aH,bW,Math.max(aH,1));
    ctx.fillStyle='#9A9088'; ctx.font='9px -apple-system,sans-serif';
    ctx.textAlign='center'; ctx.fillText(d,cx,H-8);
  }});
  ctx.strokeStyle='#E4DFDA'; ctx.lineWidth=1; ctx.setLineDash([]);
  ctx.beginPath(); ctx.moveTo(padL,padT+cH); ctx.lineTo(padL+cW,padT+cH); ctx.stroke();
}})();
</script>
</body>
</html>"""

# ── GitHub Pages ──────────────────────────────────────────────────────────────

def push_report_page(html, d):
    if not GITHUB_TOKEN:
        print("  ⚠  No GITHUB_TOKEN — skipping page publish")
        return None
    encoded = base64.b64encode(html.encode()).decode()
    headers = {"Authorization": f"token {GITHUB_TOKEN}",
               "Accept": "application/vnd.github+json",
               "Content-Type": "application/json"}
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/contents/index.html",
        headers=headers, params={"ref": "gh-pages"})
    body = {"message": f"Report {d['yd']}", "content": encoded, "branch": "gh-pages"}
    if r.ok:
        body["sha"] = r.json()["sha"]
    r2 = requests.put(
        f"https://api.github.com/repos/{GITHUB_REPO}/contents/index.html",
        headers=headers, json=body)
    if r2.ok:
        print(f"✅  Published to {PAGE_URL}")
        return PAGE_URL
    print(f"  ⚠  Page publish failed: {r2.status_code} {r2.text[:300]}")
    return None

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    d = compute_dates()
    print(f"Week: {d['week_label']}  yd={d['yd']}  mtd_start={d['mtd_start']}")

    print("Reading forecast sheet...")
    plan = get_plan(d)

    print("Connecting to Looker...")
    lk = Looker()

    print("Querying totals (6 windows)...")
    ty_yd  = lk.totals(d["yd"],          d["yd"])
    ly_yd  = lk.totals(d["ly_yd"],       d["ly_yd"])
    ty_lw  = lk.totals(d["lw_start"],    d["lw_end"])
    ly_lw  = lk.totals(d["ly_lw_start"], d["ly_lw_end"])
    # MTD range ends at yd_minus1 to avoid Looker data lag on the most recent day;
    # yesterday's single-day results are added below after all queries complete.
    ty_mtd = lk.totals(d["mtd_start"],   d["yd_minus1"])
    ly_mtd = lk.totals(d["ly_mtd_start"],d["ly_yd_minus1"])

    print("Querying store breakdown (6 queries)...")
    stores_yd      = lk.stores(d["yd"],          d["yd"])
    stores_ly_yd   = lk.stores(d["ly_yd"],       d["ly_yd"])
    stores_lw      = lk.stores(d["lw_start"],    d["lw_end"])
    stores_mtd     = lk.stores(d["mtd_start"],   d["yd_minus1"])
    stores_ly_lw   = lk.stores(d["ly_lw_start"], d["ly_lw_end"])
    stores_ly_mtd  = lk.stores(d["ly_mtd_start"],d["ly_yd_minus1"])

    print("Querying daily actuals...")
    daily_actuals = lk.daily(d["mtd_start"], d["yd_minus1"])
    # pin yesterday explicitly so the chart always includes the most recent day
    daily_actuals[d["yd"].day] = ty_yd["revenue"]

    print("Querying category mix (TY + LY)...")
    categories    = lk.categories(d["mtd_start"],    d["yd_minus1"])
    ly_categories = lk.categories(d["ly_mtd_start"], d["ly_yd_minus1"])

    print("Querying monthly trend...")
    trend_start = (d["yd"].replace(day=1) - datetime.timedelta(days=30 * 30)).replace(day=1)
    monthly_trend = lk.monthly_trend(trend_start, d["yd"])

    print("Querying audience (Trade vs B2C)...")
    aud_yd          = lk.audience(d["yd"],          d["yd"])
    aud_lw          = lk.audience(d["lw_start"],    d["lw_end"])
    aud_mtd         = lk.audience(d["mtd_start"],   d["yd_minus1"])
    aud_mtd_stores  = lk.audience_stores(d["mtd_start"], d["yd_minus1"])

    # Merge yesterday into all MTD accumulators so totals are lag-free
    for key in ("revenue", "orders", "units"):
        ty_mtd[key] += ty_yd[key]
        ly_mtd[key] += ly_yd[key]
    for store in ("soho", "denver", "dallas"):
        stores_mtd[store]   += stores_yd.get(store, 0)
        stores_ly_mtd[store] += stores_ly_yd.get(store, 0)
    for grp in ("B2C", "Trade"):
        aud_mtd[grp]["revenue"] += aud_yd[grp]["revenue"]
        aud_mtd[grp]["orders"]  += aud_yd[grp]["orders"]
    aud_yd_stores = lk.audience_stores(d["yd"], d["yd"])
    for store in ("soho", "denver", "dallas"):
        for grp in ("B2C", "Trade"):
            aud_mtd_stores[store][grp] += aud_yd_stores.get(store, {}).get(grp, 0)

    # ── Metrics ───────────────────────────────────────────────────────────────

    def sp(s, e, k="total"):
        return sum(plan.get(day, {}).get(k, 0) for day in range(s, e + 1))

    yd_day   = d["yd"].day
    yd_plan  = plan.get(yd_day, {}).get("total", 0)
    lw_plan  = sp(d["lw_start"].day, d["lw_end"].day)
    mtd_plan = sp(1, yd_day)

    mtd_rev_vly = pct(ty_mtd["revenue"], ly_mtd["revenue"])
    lw_rev_vly  = pct(ty_lw["revenue"],  ly_lw["revenue"])
    yd_rev_vly  = pct(ty_yd["revenue"],  ly_yd["revenue"])
    mtd_ord_vly = pct(ty_mtd["orders"],  ly_mtd["orders"])
    lw_ord_vly  = pct(ty_lw["orders"],   ly_lw["orders"])
    mtd_aov_vly = pct(aov_fn(ty_mtd),    aov_fn(ly_mtd))
    lw_aov_vly  = pct(aov_fn(ty_lw),     aov_fn(ly_lw))
    mtd_upt_vly = pct(upt_fn(ty_mtd),    upt_fn(ly_mtd))
    lw_upt_vly  = pct(upt_fn(ty_lw),     upt_fn(ly_lw))
    mtd_vp      = pct(ty_mtd["revenue"], mtd_plan)
    lw_vp       = pct(ty_lw["revenue"],  lw_plan)
    yd_vp       = pct(ty_yd["revenue"],  yd_plan)

    soho_vp   = pct(stores_mtd.get("soho",   0), sp(1, yd_day, "soho"))
    denver_vp = pct(stores_mtd.get("denver", 0), sp(1, yd_day, "denver"))
    dallas_vp = pct(stores_mtd.get("dallas", 0), sp(1, yd_day, "dallas"))

    # ── HTML → GitHub Pages ───────────────────────────────────────────────────

    print("Generating HTML report...")
    html = make_html(
        d, ty_yd, ty_lw, ty_mtd, ly_yd, ly_lw, ly_mtd,
        stores_yd, stores_lw, stores_mtd, stores_ly_yd, stores_ly_lw, stores_ly_mtd,
        plan, daily_actuals, categories, monthly_trend, ly_categories,
        aud_yd=aud_yd, aud_lw=aud_lw, aud_mtd=aud_mtd, aud_mtd_stores=aud_mtd_stores,
    )
    report_url = push_report_page(html, d)

    # ── Slack ─────────────────────────────────────────────────────────────────

    lw_label  = f"{d['lw_start'].strftime('%b %-d')}–{d['lw_end'].strftime('%-d')}"
    link_line = f"\n<{report_url}|View full report →>" if report_url else ""

    text = (
        f"📊 *Citizenry Retail — {d['week_label']}*{link_line}\n\n"
        f"*MTD (thru {d['yd'].strftime('%b %-d')})*\n"
        f"Revenue: *{fmtd(ty_mtd['revenue'])}*  {sign(mtd_rev_vly)} vs LY  |  {fmt_pct(mtd_vp)} vs plan\n"
        f"Orders: *{ty_mtd['orders']}*  {sign(mtd_ord_vly)} vs LY  |  "
        f"AOV: *{fmtd(aov_fn(ty_mtd))}*  {sign(mtd_aov_vly)} vs LY  |  "
        f"UPT: *{upt_fn(ty_mtd)}*  {sign(mtd_upt_vly)} vs LY\n"
        f"Trade: {fmtd(aud_mtd.get('Trade',{}).get('revenue',0))}  |  B2C: {fmtd(aud_mtd.get('B2C',{}).get('revenue',0))}\n\n"
        f"*Store Breakdown (MTD)*\n"
        f"• SoHo:   {fmtd(stores_mtd['soho'])}  ({fmt_pct(soho_vp)} vs plan)\n"
        f"• Denver: {fmtd(stores_mtd['denver'])}  ({fmt_pct(denver_vp)} vs plan)\n"
        f"• Dallas: {fmtd(stores_mtd['dallas'])}  ({fmt_pct(dallas_vp)} vs plan)\n\n"
        f"*Last Week ({lw_label})*\n"
        f"Revenue: {fmtd(ty_lw['revenue'])}  {sign(lw_rev_vly)} vs LY  |  {fmt_pct(lw_vp)} vs plan\n"
        f"Orders: {ty_lw['orders']}  {sign(lw_ord_vly)} vs LY  |  "
        f"AOV: {fmtd(aov_fn(ty_lw))}  {sign(lw_aov_vly)} vs LY  |  "
        f"UPT: {upt_fn(ty_lw)}  {sign(lw_upt_vly)} vs LY\n"
        f"Trade: {fmtd(aud_lw.get('Trade',{}).get('revenue',0))}  |  B2C: {fmtd(aud_lw.get('B2C',{}).get('revenue',0))}\n\n"
        f"*Yesterday ({d['yd'].strftime('%a %b %-d')})*\n"
        f"{fmtd(ty_yd['revenue'])}  {sign(yd_rev_vly)} vs LY  |  {fmt_pct(yd_vp)} vs plan  |  {ty_yd['orders']} orders\n"
        f"Trade: {fmtd(aud_yd.get('Trade',{}).get('revenue',0))}  |  B2C: {fmtd(aud_yd.get('B2C',{}).get('revenue',0))}"
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
