/** Plain names for factor keys, for the places that show a key without the
 *  full record beside it (the leaderboard's "stands out on" column). Kept in
 *  one place so the table and the company panel cannot drift apart. */
export const FACTOR_LABELS: Record<string, string> = {
  accruals: "earnings backed by cash",
  cash_conversion: "cash per dollar of profit",
  asset_growth: "balance sheet expansion",
  net_share_issuance: "dilution",
  gross_profitability: "gross profitability",
  return_on_assets: "return on assets",
  operating_margin_change: "margin direction",
  revenue_growth: "revenue growth",
  asset_turnover_change: "asset efficiency",
  leverage_change: "debt direction",
  cash_to_assets: "cash cushion",
};
