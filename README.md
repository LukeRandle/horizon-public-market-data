# Horizon public market data

This repository normalises **already-public World Bank Commodity Price Data (The Pink Sheet)** for personal analytical use. It contains fifteen attributed monthly commodity series and their bounded standard-library processing script.

Source: [World Bank Commodity Markets](https://www.worldbank.org/en/research/commodity-markets). Dataset licence: [Creative Commons Attribution 4.0, as stated in the World Bank catalogue](https://datacatalog.worldbank.org/search/dataset/0038238/commodity-prices-history-and-projections).

The World Bank is the original data source. Horizon Terminal extracts and normalises selected columns, retains reference months and units, and adds a separate successful source-check timestamp. This is not World Bank endorsement. These nominal US-dollar **monthly averages** are not futures contracts, executable prices or real-time data. Original observations may be revised.

The hourly GitHub Actions job discovers the current monthly workbook from the official source page. It validates the workbook, source URL, sheet, units, periods and freshness before atomically replacing `data/world-bank-cycles.json`. Failed checks preserve the previous file and its original check timestamp. Scheduled jobs may be delayed by GitHub; consumers must reject stale check timestamps.

Public data only. Horizon Terminal application code, accounts, portfolio files and provider credentials are held separately.

## Public US government headlines

`data/government-news.json` contains headline metadata and original links from
three fixed official feeds: [BLS CPI](https://www.bls.gov/feed/cpi.rss),
[BLS Employment Situation](https://www.bls.gov/feed/empsit.rss), and
[EIA press releases](https://www.eia.gov/rss/press_rss.xml). Government attribution
is retained through the feed URLs and original article links. Sources:
[BLS RSS directory](https://www.bls.gov/feed/), [BLS linking and copyright policy](https://www.bls.gov/bls/linksite.htm),
[EIA RSS directory](https://www.eia.gov/tools/rssfeeds/) and
[EIA reuse policy](https://www.eia.gov/about/copyrights_reuse.php).
These public government feeds permit attributed reuse. No article bodies,
photographs, private accounts, publisher commercial feeds or credentials are stored.

The independent standard GitHub runner checks the three original feeds every
15 minutes with a truthful Horizon user agent. It does not disguise its identity,
follow redirects or use access-control workarounds. Bounded XML validation rejects
DTD/entities, unsafe URLs, malformed dates and future-dated entries. At most
50 headlines per source are retained. Timeouts and server errors receive at most
one retry; access denials do not. Publisher `Retry-After` is respected.

`schemaVersion: 1` defines `generatedAt` and a `feeds` array with exactly the
three fixed IDs `bls-cpi`, `bls-jobs`, and `eia`. Every feed includes `feedUrl`,
`checkedAt`, `lastAttemptAt`, `failed`, and `items` (`title`, `url`, `publishedAt`).
Publication dates are source timestamps, never inferred from collection time.
Missing source dates remain null. `checkedAt` records the last successful source
fetch; failed attempts preserve its original value and last-good headlines.
`lastAttemptAt` and `generatedAt` do not make retained data newer. Consumers
must reject or visibly flag a source whose successful check is over 12 hours old
and retain failure warnings. Scheduled GitHub runs can be delayed; this is a
periodically refreshed government headline feed, not an instant newswire.

Run `python3 -m unittest discover -s tools -p 'test_*.py'` for validation and
`python3 tools/refresh-news.py` to perform an actual check. The news workflow can
only commit `data/government-news.json`; commodity publication remains separate.
