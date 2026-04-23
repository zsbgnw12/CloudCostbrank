#!/usr/bin/env bash
# Probe all documented endpoints and save JSON samples.
set -u
TOKEN=$(cat /tmp/casdoor_token.txt)
API="https://cloudcost-brank.yellowground-bf760827.southeastasia.azurecontainerapps.io"
OUT=/tmp/probe
mkdir -p "$OUT"
hit() {
  local label="$1" path="$2"
  local file="$OUT/${label}.json"
  local code
  code=$(curl -s -o "$file" -w "%{http_code}" -H "Authorization: Bearer $TOKEN" "$API$path")
  local size=$(wc -c < "$file")
  echo "[$code] ($size bytes) $label  <- $path"
}
MONTH="2026-04"
SD="2026-04-01"; ED="2026-04-19"

# §4
hit health        "/api/health"
hit sync.last     "/api/sync/last"
hit dash.bundle   "/api/dashboard/bundle?month=$MONTH"
hit dash.overview "/api/dashboard/overview?month=$MONTH"
hit mtr.summary   "/api/metering/summary?date_start=$SD&date_end=$ED"
hit mtr.daily     "/api/metering/daily?date_start=$SD&date_end=$ED"
hit mtr.byservice "/api/metering/by-service?date_start=$SD&date_end=$ED"
hit mtr.detail    "/api/metering/detail?date_start=$SD&date_end=$ED&page=1&page_size=5"
hit mtr.count     "/api/metering/detail/count?date_start=$SD&date_end=$ED"
hit bil.detail    "/api/billing/detail?date_start=$SD&date_end=$ED&page=1&page_size=5"
hit bil.count     "/api/billing/detail/count?date_start=$SD&date_end=$ED"

# §5.1 dashboard splits
hit dash.trend    "/api/dashboard/trend?start=$MONTH&end=$MONTH"
hit dash.by-prv   "/api/dashboard/by-provider?month=$MONTH"
hit dash.by-cat   "/api/dashboard/by-category?month=$MONTH"
hit dash.by-proj  "/api/dashboard/by-project?month=$MONTH&limit=5"
hit dash.by-svc   "/api/dashboard/by-service?month=$MONTH&limit=5"
hit dash.by-reg   "/api/dashboard/by-region?month=$MONTH"
hit dash.growth   "/api/dashboard/top-growth?period=7d&limit=5"
hit dash.unassn   "/api/dashboard/unassigned?month=$MONTH"

# §5.2–5.5
hit sa.list       "/api/service-accounts/?page_size=3"
hit sa.detail     "/api/service-accounts/7"
hit sa.costs      "/api/service-accounts/7/costs?start_date=$SD&end_date=$ED"
hit sa.daily      "/api/service-accounts/daily-report?start_date=$SD&end_date=$ED"

# §5.6 projects
hit proj.list     "/api/projects/?page_size=3"
hit proj.detail   "/api/projects/7"

# §5.7–5.8 bills
hit bills.list    "/api/bills/?month=$MONTH&page_size=5"
# §5.9 alerts
hit alerts.rs     "/api/alerts/rule-status?month=$MONTH"
# §5.10 suppliers
hit sup.all       "/api/suppliers/supply-sources/all"
# §5.11 metering products
hit mtr.products  "/api/metering/products"

# §6 P2
hit cat.list      "/api/categories/"
hit er.list       "/api/exchange-rates/?from_currency=USD"
hit ds.list       "/api/data-sources/"
hit res.list      "/api/resources/?page_size=3"

echo "=== DONE. files:"
ls -la "$OUT" | awk '{print $5, $9}' | head -60
