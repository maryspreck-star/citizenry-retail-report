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

# ── Dates ─────────────────────────────────────────────────────────────────────

def compute_dates():
    today     = datetime.date.today()
    yd        = today - datetime.timedelta(days=1)
    lw_end    = yd
    lw_start  = lw_end - datetime.timedelta(days=6)
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
        return self.query(
            fields=["orders.store_name", "orders.cz_actual"],
            filters={**RETAIL_FILTERS, "orders.created_date": f"{start} to {end}"},
            sorts=["orders.cz_actual desc"],
        )

    def daily(self, start, end):
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
def fmt_pct(v):  return ("+" if v is not None and v >= 0 else "") + pct_s(v) if v is not None else "–"
def clr(v):      return "#2e7d32" if v is not None and v >= 0 else "#c62828"

# ── HTML report ───────────────────────────────────────────────────────────────

def make_html(d, ty_mtd, ty_lw, ty_yd, ly_mtd, ly_lw, ly_yd,
              stores, mtd_plan, lw_plan, yd_plan, daily_actuals,
              mtd_rev_vly, lw_rev_vly, yd_rev_vly,
              mtd_ord_vly, lw_ord_vly,
              mtd_aov_vly, lw_aov_vly,
              mtd_upt_vly, lw_upt_vly,
              mtd_vp, lw_vp, yd_vp,
              soho_vp, denver_vp, dallas_vp,
              aov_fn, upt_fn):

    def krow(label, ty_val, vly, vp):
        vc = clr(vly); pc = clr(vp)
        return (f"<tr><td>{label}</td>"
                f"<td class='num'><b>{ty_val}</b></td>"
                f"<td class='num' style='color:{vc}'>{sign(vly)}</td>"
                f"<td class='num' style='color:{pc}'>{fmt_pct(vp)}</td></tr>")

    def store_row(name, rev, vp):
        total = ty_mtd["revenue"] or 1
        pct_of_total = round(rev / total * 100)
        pc = clr(vp)
        return (f"<tr><td>{name}</td>"
                f"<td class='num'><b>{fmtd(rev)}</b></td>"
                f"<td class='num' style='color:{pc}'>{fmt_pct(vp)}</td>"
                f"<td><div class='bar'><div class='fill' style='width:{pct_of_total}%'></div></div></td></tr>")

    # Simple sparkline: max day value for scaling
    max_day = max(daily_actuals.values()) if daily_actuals else 1
    days    = sorted(daily_actuals.keys())
    bars    = ""
    for day in range(1, d["yd"].day + 1):
        rev = daily_actuals.get(day, 0)
        h   = max(2, round(rev / max_day * 60))
        bars += f"<div class='dbar' style='height:{h}px' title='Day {day}: {fmtd(rev)}'></div>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Citizenry Retail — {d['week_label']}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          background: #f7f7f7; color: #1a1a1a; padding: 24px 16px; }}
  .wrap {{ max-width: 680px; margin: 0 auto; }}
  h1 {{ font-size: 1.3rem; font-weight: 700; }}
  .sub {{ color: #666; font-size: 0.85rem; margin: 4px 0 24px; }}
  .card {{ background: #fff; border-radius: 10px; padding: 16px 20px;
           margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  h2 {{ font-size: 0.95rem; font-weight: 600; color: #444;
        margin-bottom: 12px; text-transform: uppercase; letter-spacing: .04em; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
  th {{ text-align: left; font-weight: 600; padding: 5px 6px;
        border-bottom: 2px solid #eee; color: #555; font-size: 0.8rem; }}
  td {{ padding: 6px 6px; border-bottom: 1px solid #f2f2f2; }}
  .num {{ text-align: right; }}
  .bar {{ background: #eee; border-radius: 3px; height: 6px; min-width: 60px; }}
  .fill {{ background: #1a73e8; border-radius: 3px; height: 6px; }}
  .spark {{ display: flex; align-items: flex-end; gap: 3px; height: 64px;
            padding: 4px 0; margin-top: 8px; }}
  .dbar {{ flex: 1; background: #1a73e8; border-radius: 2px 2px 0 0;
           min-width: 4px; opacity: .75; cursor: default; }}
  .dbar:hover {{ opacity: 1; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Citizenry Retail</h1>
  <div class="sub">{d['week_label']} &nbsp;·&nbsp; Generated {d['today'].strftime('%b %-d, %Y')}</div>

  <div class="card">
    <h2>Month to Date &nbsp;<span style="font-weight:400;color:#888">thru {d['yd'].strftime('%b %-d')}</span></h2>
    <table>
      <tr><th>Metric</th><th class="num">TY</th><th class="num">vs LY</th><th class="num">vs Plan</th></tr>
      {krow("Revenue",  fmtd(ty_mtd["revenue"]), mtd_rev_vly, mtd_vp)}
      {krow("Orders",   str(ty_mtd["orders"]),   mtd_ord_vly, None)}
      {krow("AOV",      fmtd(aov_fn(ty_mtd)),    mtd_aov_vly, None)}
      {krow("UPT",      str(upt_fn(ty_mtd)),     mtd_upt_vly, None)}
    </table>
    <div class="spark">{bars}</div>
    <div style="font-size:.75rem;color:#999;margin-top:4px">Daily revenue — {d['mtd_start'].strftime('%b %-d')} thru {d['yd'].strftime('%-d')}</div>
  </div>

  <div class="card">
    <h2>Store Breakdown &nbsp;<span style="font-weight:400;color:#888">MTD vs plan</span></h2>
    <table>
      <tr><th>Store</th><th class="num">Revenue</th><th class="num">vs Plan</th><th style="min-width:80px">Mix</th></tr>
      {store_row("SoHo",   stores["soho"],   soho_vp)}
      {store_row("Denver", stores["denver"], denver_vp)}
      {store_row("Dallas", stores["dallas"], dallas_vp)}
    </table>
  </div>

  <div class="card">
    <h2>Last Week &nbsp;<span style="font-weight:400;color:#888">{d['lw_start'].strftime('%b %-d')}–{d['lw_end'].strftime('%-d')}</span></h2>
    <table>
      <tr><th>Metric</th><th class="num">TY</th><th class="num">vs LY</th><th class="num">vs Plan</th></tr>
      {krow("Revenue", fmtd(ty_lw["revenue"]), lw_rev_vly, lw_vp)}
      {krow("Orders",  str(ty_lw["orders"]),   lw_ord_vly, None)}
      {krow("AOV",     fmtd(aov_fn(ty_lw)),    lw_aov_vly, None)}
      {krow("UPT",     str(upt_fn(ty_lw)),     lw_upt_vly, None)}
    </table>
  </div>

  <div class="card">
    <h2>Yesterday &nbsp;<span style="font-weight:400;color:#888">{d['yd'].strftime('%a %b %-d')}</span></h2>
    <table>
      <tr><th>Metric</th><th class="num">TY</th><th class="num">vs LY</th><th class="num">vs Plan</th></tr>
      {krow("Revenue", fmtd(ty_yd["revenue"]), yd_rev_vly, yd_vp)}
      {krow("Orders",  str(ty_yd["orders"]),   None,       None)}
      {krow("AOV",     fmtd(aov_fn(ty_yd)),    None,       None)}
    </table>
  </div>
</div>
</body>
</html>"""
    return html


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
    else:
        print(f"  ⚠  Page publish failed: {r2.status_code} {r2.text[:300]}")
        return None

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    d = compute_dates()
    print(f"Week: {d['week_label']}  yd={d['yd']}  mtd_start={d['mtd_start']}")

    print("Reading forecast sheet...")
    plan   = get_plan(d)
    yd_day = d["yd"].day

    def sp(s, e, k="total"): return sum(plan.get(day, {}).get(k, 0) for day in range(s, e + 1))
    yd_plan  = plan.get(yd_day, {"soho": 0, "denver": 0, "dallas": 0, "total": 0})
    lw_plan  = {k: sp(d["lw_start"].day, d["lw_end"].day, k) for k in ("soho","denver","dallas","total")}
    mtd_plan = {k: sp(1, yd_day, k) for k in ("soho","denver","dallas","total")}

    print("Connecting to Looker...")
    lk = Looker()

    print("Querying revenue/orders/units (6 windows)...")
    ty_yd  = lk.totals(d["yd"],          d["yd"])
    ly_yd  = lk.totals(d["ly_yd"],       d["ly_yd"])
    ty_lw  = lk.totals(d["lw_start"],    d["lw_end"])
    ly_lw  = lk.totals(d["ly_lw_start"], d["ly_lw_end"])
    ty_mtd = lk.totals(d["mtd_start"],   d["yd"])
    ly_mtd = lk.totals(d["ly_mtd_start"],d["ly_yd"])

    print("Querying store breakdown...")
    store_rows = lk.stores(d["mtd_start"], d["yd"])

    print("Querying daily actuals...")
    daily_actuals = lk.daily(d["mtd_start"], d["yd"])

    # ── Metrics ───────────────────────────────────────────────────────────────

    def aov_fn(r): return round(r["revenue"] / r["orders"], 2) if r["orders"] else 0
    def upt_fn(r): return round(r["units"]   / r["orders"], 2) if r["orders"] else 0

    yd_rev_vly  = pct(ty_yd["revenue"],  ly_yd["revenue"])
    lw_rev_vly  = pct(ty_lw["revenue"],  ly_lw["revenue"])
    mtd_rev_vly = pct(ty_mtd["revenue"], ly_mtd["revenue"])
    lw_ord_vly  = pct(ty_lw["orders"],   ly_lw["orders"])
    mtd_ord_vly = pct(ty_mtd["orders"],  ly_mtd["orders"])
    lw_aov_vly  = pct(aov_fn(ty_lw),     aov_fn(ly_lw))
    mtd_aov_vly = pct(aov_fn(ty_mtd),    aov_fn(ly_mtd))
    lw_upt_vly  = pct(upt_fn(ty_lw),     upt_fn(ly_lw))
    mtd_upt_vly = pct(upt_fn(ty_mtd),    upt_fn(ly_mtd))

    yd_vp  = pct(ty_yd["revenue"],  yd_plan["total"])
    lw_vp  = pct(ty_lw["revenue"],  lw_plan["total"])
    mtd_vp = pct(ty_mtd["revenue"], mtd_plan["total"])

    stores = {"soho": 0.0, "denver": 0.0, "dallas": 0.0}
    for row in store_rows:
        key = STORE_MAP.get(row.get("orders.store_name", ""))
        if key: stores[key] = float(row.get("orders.cz_actual", 0) or 0)

    soho_vp   = pct(stores["soho"],   mtd_plan.get("soho",   0))
    denver_vp = pct(stores["denver"], mtd_plan.get("denver", 0))
    dallas_vp = pct(stores["dallas"], mtd_plan.get("dallas", 0))

    # ── HTML report → GitHub Pages ────────────────────────────────────────────

    print("Generating HTML report...")
    html = make_html(
        d, ty_mtd, ty_lw, ty_yd, ly_mtd, ly_lw, ly_yd,
        stores, mtd_plan, lw_plan, yd_plan, daily_actuals,
        mtd_rev_vly, lw_rev_vly, yd_rev_vly,
        mtd_ord_vly, lw_ord_vly,
        mtd_aov_vly, lw_aov_vly,
        mtd_upt_vly, lw_upt_vly,
        mtd_vp, lw_vp, yd_vp,
        soho_vp, denver_vp, dallas_vp,
        aov_fn, upt_fn,
    )
    report_url = push_report_page(html, d)

    # ── Slack ─────────────────────────────────────────────────────────────────

    lw_label = f"{d['lw_start'].strftime('%b %-d')}–{d['lw_end'].strftime('%-d')}"
    link_line = f"\n<{report_url}|View full report →>" if report_url else ""

    text = (
        f"📊 *Citizenry Retail — {d['week_label']}*{link_line}\n\n"

        f"*MTD (thru {d['yd'].strftime('%b %-d')})*\n"
        f"Revenue: *{fmtd(ty_mtd['revenue'])}*  {sign(mtd_rev_vly)} vs LY  |  {fmt_pct(mtd_vp)} vs plan\n"
        f"Orders: *{ty_mtd['orders']}*  {sign(mtd_ord_vly)} vs LY  |  "
        f"AOV: *{fmtd(aov_fn(ty_mtd))}*  {sign(mtd_aov_vly)} vs LY  |  "
        f"UPT: *{upt_fn(ty_mtd)}*  {sign(mtd_upt_vly)} vs LY\n\n"

        f"*Store Breakdown (MTD)*\n"
        f"• SoHo:   {fmtd(stores['soho'])}  ({fmt_pct(soho_vp)} vs plan)\n"
        f"• Denver: {fmtd(stores['denver'])}  ({fmt_pct(denver_vp)} vs plan)\n"
        f"• Dallas: {fmtd(stores['dallas'])}  ({fmt_pct(dallas_vp)} vs plan)\n\n"

        f"*Last Week ({lw_label})*\n"
        f"Revenue: {fmtd(ty_lw['revenue'])}  {sign(lw_rev_vly)} vs LY  |  {fmt_pct(lw_vp)} vs plan\n"
        f"Orders: {ty_lw['orders']}  {sign(lw_ord_vly)} vs LY  |  "
        f"AOV: {fmtd(aov_fn(ty_lw))}  {sign(lw_aov_vly)} vs LY  |  "
        f"UPT: {upt_fn(ty_lw)}  {sign(lw_upt_vly)} vs LY\n\n"

        f"*Yesterday ({d['yd'].strftime('%a %b %-d')})*\n"
        f"{fmtd(ty_yd['revenue'])}  {sign(yd_rev_vly)} vs LY  |  {fmt_pct(yd_vp)} vs plan  |  {ty_yd['orders']} orders"
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
