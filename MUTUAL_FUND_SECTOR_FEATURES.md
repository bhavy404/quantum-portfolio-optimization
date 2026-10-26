# Mutual-fund dataset and automatic sector weights

## Mutual funds
- `portal/assets/mutual_fund_data.csv` is the supplied catalogue (16,390 scheme records).
- The Mutual Funds page provides filters for scheme category, AMC/fund house, scheme type, and a text search, plus a scheme dropdown for adding up to five funds to comparison.
- The CSV contains scheme metadata and a point-in-time NAV snapshot, not a full NAV time series. Its latest snapshot date in this dataset is 2026-08-12.
- The comparison table fetches NAV history from MFAPI when available to calculate 1-year return and 3-/5-year CAGR. Some schemes may not be available from that external source.

## Automatic sector weights
- `/api/sector-recommendations` calculates average 3-month and 1-year price returns for QPortal's curated starter stocks grouped by their current sector tags.
- It uses 35% weight for 3-month return and 65% for 1-year return when both are available, renormalizing when only one horizon is available.
- The return-based score is `max(0.05, 1 + 3 * momentum)`, then normalized to 100%. This is a transparent heuristic, not a statistically validated optimizer.
- The Sector Mix page initially selects up to five sectors with the strongest available momentum. Users may add/remove sectors; weights are automatically renormalized over the chosen sectors.
- Within each sector, the display divides that sector's allocation equally among up to four example stocks.

## Limitations
- Historical momentum does not predict future returns. Sector tags are broad and the starter universe is not a complete sector index.
- NAV snapshot data, historical returns, and stock prices may be delayed or incomplete. This application is educational and does not place trades or provide personalized financial advice.
