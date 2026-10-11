# Horizon public commodity data

This repository normalises **already-public World Bank Commodity Price Data (The Pink Sheet)** for personal analytical use. It contains fifteen attributed monthly commodity series and their bounded standard-library processing script.

Source: [World Bank Commodity Markets](https://www.worldbank.org/en/research/commodity-markets). Dataset licence: [Creative Commons Attribution 4.0, as stated in the World Bank catalogue](https://datacatalog.worldbank.org/search/dataset/0038238/commodity-prices-history-and-projections).

The World Bank is the original data source. Horizon Terminal extracts and normalises selected columns, retains reference months and units, and adds a separate successful source-check timestamp. This is not World Bank endorsement. These nominal US-dollar **monthly averages** are not futures contracts, executable prices or real-time data. Original observations may be revised.

The hourly GitHub Actions job discovers the current monthly workbook from the official source page. It validates the workbook, source URL, sheet, units, periods and freshness before atomically replacing `data/world-bank-cycles.json`. Failed checks preserve the previous file and its original check timestamp. Scheduled jobs may be delayed by GitHub; consumers must reject stale check timestamps.

Public data only. Horizon Terminal application code, accounts, portfolio files and provider credentials are held separately.
