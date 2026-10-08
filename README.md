# House prices (England & Wales)

A personal tool showing sold prices near you, area averages and trends. Published on GitHub Pages and rebuilt daily by GitHub Actions (see .github/workflows/publish.yml); publish.sh does the one-time setup.

## Run it locally
1. `pip install numpy`
2. `python3 build.py` downloads about 1.2 GB, takes about 4 minutes and writes site/data/.
3. `cd site && python3 -m http.server 18937`
4. Open http://127.0.0.1:18937/

## Data (all Open Government Licence v3.0)
- **HM Land Registry Price Paid Data:** yearly files from the year 6 years ago to now, refreshed monthly by HMLR.
  - Area figures use standard (category A) sales.
  - The sales list also shows category B sales, with a tag.
- **UK House Price Index full file:** regional, local authority and UK averages.
- **OS Code-Point Open:** postcode locations, used for distances.
- **postcodes.io:** maps each local authority to its region at build time, and does postcode and town search in the browser.
- **Nominatim (OpenStreetMap):** street search in the browser, one request per search.

## Output
- `site/data/d/<district>.json`, one per postcode district, holding:
  - 12-month median and mean, by property type
  - 1, 3 and 5 years ago
  - rolling 12-month median series
  - sector stats
  - the last 24 months of individual sales, capped at 2,500
- `meta.json`: England & Wales and region figures.
- `la.json`, `hpi.json`, `districts.json`.

## Not included
Scotland and Northern Ireland have no open price-paid data.

## Publishing later
`publish.sh` and `.github/workflows/publish.yml` are ready but have **not** been run.
- They would publish to GitHub Pages and refresh daily, re-using cached downloads while HMLR's files are unchanged.
- They include the 30-day keep-alive.
