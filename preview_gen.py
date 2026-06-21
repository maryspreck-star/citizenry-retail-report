"""Generate preview HTML from live Looker data — no Slack, no GitHub push."""
import sys, datetime, os
os.environ.setdefault("LOOKER_BASE_URL","x"); os.environ.setdefault("LOOKER_CLIENT_ID","x")
os.environ.setdefault("LOOKER_CLIENT_SECRET","x"); os.environ.setdefault("SLACK_WEBHOOK_URL","x")
sys.path.insert(0, '/Users/mc.spreck/Claude/citizenry-retail-report')
from report import make_html, compute_dates, get_plan

d = compute_dates()

# ── Plan data (real from Google Sheet) ────────────────────────────────────────
plan = get_plan(d)

# ── TY totals (Jun 1–20, 2026) ────────────────────────────────────────────────
ty_yd  = {"revenue": 623,      "orders": 4,   "units": 5}
ly_yd  = {"revenue": 3554,     "orders": 5,   "units": 19}
ty_lw  = {"revenue": 9325.49,  "orders": 23,  "units": 44}
ly_lw  = {"revenue": 32388.95, "orders": 47,  "units": 98}
ty_mtd = {"revenue": 54057,    "orders": 93,  "units": 177}
ly_mtd = {"revenue": 87137.39, "orders": 154, "units": 286}

# ── Stores ────────────────────────────────────────────────────────────────────
stores_yd     = {"soho": 474,    "denver": 0,       "dallas": 149}
stores_ly_yd  = {"soho": 2956,   "denver": 0,       "dallas": 598}
stores_lw     = {"soho": 6964,   "denver": 1531,    "dallas": 830}
stores_mtd    = {"soho": 40391,  "denver": 10890,   "dallas": 2153}
stores_ly_lw  = {"soho": 26216,  "denver": 3940,    "dallas": 2233}
stores_ly_mtd = {"soho": 74742,  "denver": 8462,    "dallas": 3933}

# ── Daily actuals (Jun 1–20) ──────────────────────────────────────────────────
daily_actuals = {1:6282,2:6234,3:1097,4:160,5:4167,6:4008,7:1068,
                 8:7435,9:3463,10:1334,11:765,12:7494,13:602,
                 14:4034,15:3520,16:412,17:786,18:574,19:0,20:623}

# ── TY Categories (MTD Jun 1–20, 2026) ───────────────────────────────────────
categories = [
    {"products.product_type":"Rugs",      "orders.cz_actual":20455,"order_items.number_of_items":11},
    {"products.product_type":"Bedding",   "orders.cz_actual":17229,"order_items.number_of_items":88},
    {"products.product_type":"Furniture", "orders.cz_actual":12368,"order_items.number_of_items":7},
    {"products.product_type":"Accents",   "orders.cz_actual":5591, "order_items.number_of_items":4},
    {"products.product_type":"Pillows",   "orders.cz_actual":5095, "order_items.number_of_items":34},
    {"products.product_type":"Bath",      "orders.cz_actual":1242, "order_items.number_of_items":20},
    {"products.product_type":"Blankets",  "orders.cz_actual":931,  "order_items.number_of_items":4},
    {"products.product_type":"Baskets",   "orders.cz_actual":247,  "order_items.number_of_items":2},
    {"products.product_type":"Tableware", "orders.cz_actual":113,  "order_items.number_of_items":2},
]

# ── LY Categories (MTD Jun 1–20, 2025) — real Looker data ────────────────────
ly_categories = [
    {"products.product_type":"Bedding",   "orders.cz_actual":30497,"order_items.number_of_items":127},
    {"products.product_type":"Rugs",      "orders.cz_actual":23502,"order_items.number_of_items":13},
    {"products.product_type":"Furniture", "orders.cz_actual":16111,"order_items.number_of_items":10},
    {"products.product_type":"Pillows",   "orders.cz_actual":11751,"order_items.number_of_items":62},
    {"products.product_type":"Blankets",  "orders.cz_actual":5410, "order_items.number_of_items":12},
    {"products.product_type":"Bath",      "orders.cz_actual":4243, "order_items.number_of_items":25},
    {"products.product_type":"Tableware", "orders.cz_actual":3336, "order_items.number_of_items":13},
    {"products.product_type":"Accents",   "orders.cz_actual":3208, "order_items.number_of_items":5},
    {"products.product_type":"Baskets",   "orders.cz_actual":1651, "order_items.number_of_items":8},
]

# ── Monthly trend — TY (2026) + LY comparisons (2025 + 2024 real Looker data) ─
monthly_trend = [
    # 2026 TY
    {"orders.created_month":"2026-06","orders.cz_actual":54057, "orders.num_orders":93, "order_items.number_of_items":177},
    {"orders.created_month":"2026-05","orders.cz_actual":123234,"orders.num_orders":198,"order_items.number_of_items":372},
    {"orders.created_month":"2026-04","orders.cz_actual":109035,"orders.num_orders":168,"order_items.number_of_items":315},
    {"orders.created_month":"2026-03","orders.cz_actual":138276,"orders.num_orders":188,"order_items.number_of_items":314},
    {"orders.created_month":"2026-02","orders.cz_actual":122821,"orders.num_orders":156,"order_items.number_of_items":264},
    {"orders.created_month":"2026-01","orders.cz_actual":104383,"orders.num_orders":156,"order_items.number_of_items":267},
    # 2025 (used as TY rows + LY for 2026 months)
    {"orders.created_month":"2025-12","orders.cz_actual":137210,"orders.num_orders":211,"order_items.number_of_items":373},
    {"orders.created_month":"2025-11","orders.cz_actual":176479,"orders.num_orders":274,"order_items.number_of_items":605},
    {"orders.created_month":"2025-10","orders.cz_actual":115169,"orders.num_orders":231,"order_items.number_of_items":447},
    {"orders.created_month":"2025-09","orders.cz_actual":162536,"orders.num_orders":255,"order_items.number_of_items":472},
    {"orders.created_month":"2025-08","orders.cz_actual":177109,"orders.num_orders":284,"order_items.number_of_items":563},
    {"orders.created_month":"2025-07","orders.cz_actual":155839,"orders.num_orders":244,"order_items.number_of_items":551},
    {"orders.created_month":"2025-06","orders.cz_actual":133438,"orders.num_orders":239,"order_items.number_of_items":498},
    {"orders.created_month":"2025-05","orders.cz_actual":194965,"orders.num_orders":331,"order_items.number_of_items":648},
    {"orders.created_month":"2025-04","orders.cz_actual":133833,"orders.num_orders":225,"order_items.number_of_items":439},
    {"orders.created_month":"2025-03","orders.cz_actual":232000,"orders.num_orders":342,"order_items.number_of_items":677},
    {"orders.created_month":"2025-02","orders.cz_actual":202370,"orders.num_orders":289,"order_items.number_of_items":524},
    {"orders.created_month":"2025-01","orders.cz_actual":164870,"orders.num_orders":270,"order_items.number_of_items":540},
    # 2024 — real Looker data, used only for vs LY on 2025 rows
    {"orders.created_month":"2024-12","orders.cz_actual":180074,"orders.num_orders":289,"order_items.number_of_items":506},
    {"orders.created_month":"2024-11","orders.cz_actual":245743,"orders.num_orders":389,"order_items.number_of_items":819},
    {"orders.created_month":"2024-10","orders.cz_actual":181803,"orders.num_orders":269,"order_items.number_of_items":558},
    {"orders.created_month":"2024-09","orders.cz_actual":214103,"orders.num_orders":331,"order_items.number_of_items":674},
    {"orders.created_month":"2024-08","orders.cz_actual":205985,"orders.num_orders":359,"order_items.number_of_items":694},
    {"orders.created_month":"2024-07","orders.cz_actual":162984,"orders.num_orders":257,"order_items.number_of_items":474},
    {"orders.created_month":"2024-06","orders.cz_actual":143149,"orders.num_orders":229,"order_items.number_of_items":447},
    {"orders.created_month":"2024-05","orders.cz_actual":288928,"orders.num_orders":364,"order_items.number_of_items":717},
    {"orders.created_month":"2024-04","orders.cz_actual":164409,"orders.num_orders":268,"order_items.number_of_items":525},
    {"orders.created_month":"2024-03","orders.cz_actual":130037,"orders.num_orders":201,"order_items.number_of_items":351},
    {"orders.created_month":"2024-02","orders.cz_actual":143984,"orders.num_orders":238,"order_items.number_of_items":447},
    {"orders.created_month":"2024-01","orders.cz_actual":151702,"orders.num_orders":209,"order_items.number_of_items":421},
]

# ── Audience (Trade vs B2C) — real Looker data ───────────────────────────────
# Jun 20 yesterday: all sales were B2C (no Trade that day)
aud_yd  = {"B2C": {"revenue": 623, "orders": 4}, "Trade": {"revenue": 0, "orders": 0}}
# Jun 14–20 last week
aud_lw  = {"B2C": {"revenue": 5127, "orders": 11}, "Trade": {"revenue": 3899, "orders": 11}}
# Jun 1–20 MTD
aud_mtd = {"B2C": {"revenue": 36705, "orders": 66}, "Trade": {"revenue": 17057, "orders": 24}}
# MTD by store
aud_mtd_stores = {
    "soho":   {"B2C": 30903, "Trade": 10115},
    "denver": {"B2C":  3948, "Trade":  6942},
    "dallas": {"B2C":  1854, "Trade":     0},
}

html = make_html(
    d, ty_yd, ty_lw, ty_mtd, ly_yd, ly_lw, ly_mtd,
    stores_yd, stores_lw, stores_mtd, stores_ly_yd, stores_ly_lw, stores_ly_mtd,
    plan, daily_actuals, categories, monthly_trend, ly_categories,
    aud_yd=aud_yd, aud_lw=aud_lw, aud_mtd=aud_mtd, aud_mtd_stores=aud_mtd_stores,
)

out = '/Users/mc.spreck/Claude/output/citizenry-retail/preview.html'
open(out, 'w').write(html)
print(f"Written to {out}")
