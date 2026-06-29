---
name: citizenry-retail-report
description: Citizenry Retail analyst — answer data questions using Looker MCP and/or generate the full weekly HTML report with Slack post. Covers SoHo, Denver, Dallas retail stores. Use for any ad-hoc analysis or to run the report outside of the Monday cron.
---

# Citizenry Retail — Analyst & Report Skill

When invoked, greet the user and ask what they'd like to do:

> "Hi! I'm your Citizenry Retail analyst. I can:
> 1. **Answer a data question** — pull any metric from Looker for any date range, store, or category
> 2. **Run the full weekly report** — generate the HTML report and optionally post to #salesoperations Slack
>
> What would you like to do?"

Handle both modes below.

---

## Looker Reference

**Model:** `citizenry` | **Explore:** `orders`
**Tool:** `mcp__looker__query`

**Always apply for retail-only data:**
```json
{"orders.order_channel": "Retail"}
```

**Key fields:**

| Field | What it is |
|---|---|
| `orders.cz_actual` | Revenue (post-discount, excl. tax/shipping) |
| `orders.num_orders` | Order count |
| `order_items.number_of_items` | Units sold |
| `orders.store_name` | Store name (use store map below) |
| `orders.created_date` | Date filter — use `"YYYY-MM-DD"` for single day, `"YYYY-MM-DD to YYYY-MM-DD"` for range |
| `orders.created_month` | Month filter — use `"YYYY-MM"` |
| `products.product_type` | Category (Bedding, Rugs, Furniture, Pillows, Blankets, Bath, Accents, Baskets, Tableware) |
| `orders.order_channel` | `Retail` = physical stores only; omit filter for all channels |

**Store name map:**
- SoHo / New York → `New York - Flagship`
- Denver → `Interior Define Studio - Denver CO1`
- Dallas → `The Citizenry Dallas TX - TX2`

**Single-day filter bug:** `"2026-06-20 to 2026-06-20"` returns 0 rows. For a single day always use just `"2026-06-20"`.

---

## Mode 1: Answer a Data Question

For any ad-hoc question, use `mcp__looker__query` to pull the relevant data. Common patterns:

### Revenue / Orders for any period
```json
{
  "model": "citizenry", "explore": "orders",
  "fields": ["orders.cz_actual", "orders.num_orders", "order_items.number_of_items"],
  "filters": {"orders.order_channel": "Retail", "orders.created_date": "<date range>"}
}
```

### By store
Add `"orders.store_name"` to fields. Filter to a specific store with `"orders.store_name": "New York - Flagship"`.

### By category
Add `"products.product_type"` to fields, sort by `"orders.cz_actual desc"`.

### Monthly trend
Use `"orders.created_month"` as a field, filter by `"orders.created_date"` with a multi-month range, sort by `"orders.created_month desc"`.

### Year-over-year comparison
Run two queries — one for TY dates, one for LY dates (same dates, prior year). Compute pct change in Python: `round((ty/ly - 1) * 100, 1)`.

**After pulling data:**
- Format numbers clearly ($X,XXX for revenue, +/− X% for changes)
- Highlight what's notable — what's up, what's down, anything surprising
- Offer a follow-up: "Want me to break this down by store / category / week?"

---

## Mode 2: Run the Full Report

### Step 1: Compute Dates

```bash
python3 -c "
import datetime, json
today = datetime.date.today()
yd = today - datetime.timedelta(days=1)
yd_minus1 = yd - datetime.timedelta(days=1)
lw_end = yd
lw_start = lw_end - datetime.timedelta(days=6)
mtd_start = yd.replace(day=1)
def ly(dt): return dt.replace(year=dt.year - 1)
print(json.dumps({
    'today': str(today), 'yd': str(yd),
    'yd_minus1': str(yd_minus1),
    'lw_start': str(lw_start), 'lw_end': str(lw_end),
    'mtd_start': str(mtd_start),
    'ly_yd': str(ly(yd)),
    'ly_lw_start': str(ly(lw_start)), 'ly_lw_end': str(ly(lw_end)),
    'ly_mtd_start': str(ly(mtd_start)),
    'week_label': f\"Week of {lw_start.strftime('%b %-d')}–{lw_end.strftime('%-d, %Y')}\",
}, indent=2))
"
```

### Step 2: Query Looker — TY + LY Totals (6 queries)

For each period (yesterday, last week, MTD) run TY and LY. Fields: `["orders.cz_actual", "orders.num_orders", "order_items.number_of_items"]`. Filter: `{"orders.order_channel": "Retail", "orders.created_date": "<date>"}`.

Single-day = `"YYYY-MM-DD"`, range = `"YYYY-MM-DD to YYYY-MM-DD"`.

**⚠️ Data lag rule — MTD queries must use `{mtd_start} to {yd_minus1}` (NOT `to {yd}`).**
Looker range queries ending on `yd` can miss late-arriving orders from that day. Yesterday is always queried as a separate single-day filter and added to MTD after all queries complete (see Step 2b). LY MTD uses `{ly_mtd_start} to {ly_yd_minus1}` for the same reason.

### Step 2b: Merge Yesterday into MTD Totals

After all queries complete, add yesterday's single-day results to the MTD totals and store breakdown:

```python
# MTD range ended at yd_minus1 — add yesterday to get true MTD
ty_mtd["revenue"] += ty_yd["revenue"]
ty_mtd["orders"]  += ty_yd["orders"]
ty_mtd["units"]   += ty_yd["units"]
ly_mtd["revenue"] += ly_yd["revenue"]
ly_mtd["orders"]  += ly_yd["orders"]
ly_mtd["units"]   += ly_yd["units"]

# Same for stores
for store in ("soho", "denver", "dallas"):
    stores_mtd[store]    += stores_yd[store]
    stores_ly_mtd[store] += stores_ly_yd[store]

# Same for audience
for grp in ("B2C", "Trade"):
    aud_mtd[grp]["revenue"] += aud_yd[grp]["revenue"]
    aud_mtd[grp]["orders"]  += aud_yd[grp]["orders"]
    aud_mtd_stores["soho"][grp] += aud_yd_stores["soho"][grp]   # etc per store
```

### Step 3: Query Looker — Stores (6 queries)

TY + LY for each of yesterday / last week / MTD. Fields: `["orders.store_name", "orders.cz_actual"]`. Same retail filter + date filter. **MTD uses `{mtd_start} to {yd_minus1}`** (yesterday added in Step 2b).

Build dicts:
```python
STORE_MAP = {
    "New York - Flagship": "soho",
    "Interior Define Studio - Denver CO1": "denver",
    "The Citizenry Dallas TX - TX2": "dallas",
}
stores_yd = {"soho": 0.0, "denver": 0.0, "dallas": 0.0}
for row in result:
    k = STORE_MAP.get(row["orders.store_name"])
    if k:
        stores_yd[k] = float(row["orders.cz_actual"] or 0)
```

### Step 4: Query Looker — Daily, Categories, Trend

**Daily actuals (MTD):** fields `["orders.created_date", "orders.cz_actual"]`, filter `{mtd_start} to {yd_minus1}`, sort by date. Then add yesterday manually:
```python
daily_actuals = {int(row["orders.created_date"].split("-")[2]): float(row["orders.cz_actual"] or 0) for row in result}
daily_actuals[int(yd.split("-")[2])] = ty_yd["revenue"]  # add yesterday
```

**TY Categories:** fields `["products.product_type", "orders.cz_actual", "order_items.number_of_items"]`, filter `{mtd_start} to {yd_minus1}`, sort `orders.cz_actual desc`.

**LY Categories:** same, dates `{ly_mtd_start} to {ly_yd_minus1}`.

**Monthly Trend (30-month window):** fields `["orders.created_month", "orders.cz_actual", "orders.num_orders", "order_items.number_of_items"]`, date filter = 30 months back to `{yd}`, sort `orders.created_month desc`, limit 48.

### Step 5: Query Looker — Audience (Trade vs B2C)

Run 4 audience queries. Fields: `["customers.customer_group", "orders.cz_actual", "orders.num_orders"]`. Retail filter + date filter.

```python
def parse_audience(rows):
    result = {"B2C": {"revenue": 0.0, "orders": 0}, "Trade": {"revenue": 0.0, "orders": 0}}
    for row in rows:
        grp = row.get("customers.customer_group") or "B2C"
        if grp in result:
            result[grp]["revenue"] += float(row.get("orders.cz_actual", 0) or 0)
            result[grp]["orders"]  += int(row.get("orders.num_orders", 0) or 0)
    return result

aud_yd  = parse_audience(result_yd_audience)    # date: yd (single day)
aud_lw  = parse_audience(result_lw_audience)    # date: lw_start to lw_end
aud_mtd = parse_audience(result_mtd_audience)   # date: mtd_start to yd_minus1 — yesterday added in Step 2b
```

**Audience by store (MTD only):** fields `["orders.store_name", "customers.customer_group", "orders.cz_actual"]`, filter `{mtd_start} to {yd_minus1}`. Yesterday store audience added in Step 2b.

```python
STORE_MAP = {"New York - Flagship": "soho", "Interior Define Studio - Denver CO1": "denver", "The Citizenry Dallas TX - TX2": "dallas"}
aud_mtd_stores = {k: {"B2C": 0.0, "Trade": 0.0} for k in STORE_MAP.values()}
for row in result_aud_stores:
    sk  = STORE_MAP.get(row.get("orders.store_name", ""))
    grp = row.get("customers.customer_group") or "B2C"
    if sk and grp in ("B2C", "Trade"):
        aud_mtd_stores[sk][grp] += float(row.get("orders.cz_actual", 0) or 0)
```

### Step 6: Fetch Plan (Google Sheets)

```bash
python3 -c "
import sys, os, pathlib, json
repo = str(pathlib.Path.home() / 'Claude/citizenry-retail-report')
sys.path.insert(0, repo)
os.environ.setdefault('LOOKER_BASE_URL','x')
os.environ.setdefault('LOOKER_CLIENT_ID','x')
os.environ.setdefault('LOOKER_CLIENT_SECRET','x')
os.environ.setdefault('SLACK_WEBHOOK_URL','x')
from report import get_plan, compute_dates
d = compute_dates()
p = get_plan(d)
print(json.dumps(p, indent=2))
"
```

### Step 7: Generate HTML

```bash
python3 /tmp/gen_report.py
```

Write `/tmp/gen_report.py` with:
```python
import sys, os, pathlib
repo = str(pathlib.Path.home() / 'Claude/citizenry-retail-report')
sys.path.insert(0, repo)
os.environ.setdefault('LOOKER_BASE_URL','x')
os.environ.setdefault('LOOKER_CLIENT_ID','x')
os.environ.setdefault('LOOKER_CLIENT_SECRET','x')
os.environ.setdefault('SLACK_WEBHOOK_URL','x')
from report import make_html, compute_dates, get_plan

d = compute_dates()
plan = get_plan(d)

# Insert all data collected in Steps 2–5 here
html = make_html(
    d, ty_yd, ty_lw, ty_mtd, ly_yd, ly_lw, ly_mtd,
    stores_yd, stores_lw, stores_mtd,
    stores_ly_yd, stores_ly_lw, stores_ly_mtd,
    plan, daily_actuals, categories, monthly_trend, ly_categories,
    aud_yd=aud_yd, aud_lw=aud_lw, aud_mtd=aud_mtd, aud_mtd_stores=aud_mtd_stores,
)
out = pathlib.Path.home() / 'Claude/output/citizenry-retail/preview.html'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(html)
print(f'Written to {out}')
```

### Step 8: Publish Artifact

Use the `Artifact` tool:
- `file_path`: `~/Claude/output/citizenry-retail/preview.html` (expand to full path)
- `favicon`: `📊`
- `url`: `https://claude.ai/code/artifact/5dc1f46d-2942-415a-9f14-dabebda44308`

### Step 9: Ask — Post to Slack?

Ask the user: "Report is ready! Want me to post this to #salesoperations?"

If yes, use `mcp__claude_ai_Slack__slack_send_message`:
- `channel_id`: `C05CJH674S3`

Message:
```
📊 *Citizenry Retail — {week_label}*
<{artifact_url}|View full report →>

*MTD (thru {yd formatted})*
Revenue: *${mtd_rev:,.0f}*  {▲/▼} {abs(mtd_vly):.0f}% vs LY  |  {mtd_vplan:+.0f}% vs plan
Orders: *{mtd_orders}*  {▲/▼} {abs(mtd_orders_vly):.0f}% vs LY  |  AOV: *${mtd_aov:,.0f}*  {▲/▼} {abs(mtd_aov_vly):.0f}% vs LY
Trade: ${mtd_trade_rev:,.0f} ({mtd_trade_pct}%)  |  B2C: ${mtd_b2c_rev:,.0f} ({mtd_b2c_pct}%)

*Store Breakdown (MTD)*
• SoHo:   ${soho_mtd:,.0f}  ({soho_vplan:+.0f}% vs plan)
• Denver: ${denver_mtd:,.0f}  ({denver_vplan:+.0f}% vs plan)
• Dallas: ${dallas_mtd:,.0f}  ({dallas_vplan:+.0f}% vs plan)

*Last Week ({lw_start} – {lw_end})*
Revenue: ${lw_rev:,.0f}  {▲/▼} {abs(lw_vly):.0f}% vs LY  |  {lw_vplan:+.0f}% vs plan
Trade: ${lw_trade_rev:,.0f} ({lw_trade_pct}%)  |  B2C: ${lw_b2c_rev:,.0f} ({lw_b2c_pct}%)

*Yesterday ({yd formatted})*
${yd_rev:,.0f}  {▲/▼} {abs(yd_vly):.0f}% vs LY  |  {yd_vplan:+.0f}% vs plan  |  {yd_orders} orders
Trade: ${yd_trade_rev:,.0f}  |  B2C: ${yd_b2c_rev:,.0f}
```

---

## Context

- **Monday autonomous run:** GitHub Actions fires every Monday 8am CT at `maryspreck-star/citizenry-retail-report` — this skill is for ad-hoc use only
- **GitHub Pages:** https://maryspreck-star.github.io/citizenry-retail-report/
- **Plan data:** Google Sheet `1NMb41PXvSsxmj1zeelgARJRE7cEeXYepyfYkQgGy75A` — "For Claude" (leftmost) tab, columns: Date / Soho Plan / Denver Plan / Dallas Plan
- **LY comparison:** Calendar year (replace year − 1)
- **Revenue field:** `orders.cz_actual` = post-discount revenue net of tax and shipping
