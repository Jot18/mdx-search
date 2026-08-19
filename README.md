# MDX Search — new Acura MDX around Yuba City

A single-file tool. It scrapes the nearest Acura dealers (Niello Acura in
Roseville, Elk Grove Acura) for new MDX listings and prints price, MSRP,
discount, trim, and a link — no API key, no credit card.

## Run
```bash
pip install -r requirements.txt
python mdx_search.py            # table of what's for sale now
python mdx_search.py --json     # machine-readable
python mdx_search.py --debug    # show scraping diagnostics
```

Each run also writes `mdx_listings_YYYY-MM-DD.csv` (every car) and appends a
summary row to `mdx_history.csv` (count, median price, median discount) so you
build a price trend over time just by running it.

## Add dealers
Edit the `DEALERS` list at the top of `mdx_search.py` — name, city, and one or
more inventory URLs (point them at the New/MDX filtered page).

## Track it automatically (optional)
Schedule it on SGN-PC29 with Task Scheduler, e.g. twice a week:
```
pythonw.exe C:\path\to\mdx_search.py
```
It appends to `mdx_history.csv` each run.

## If a dealer returns 0
Some dealer sites render inventory with JavaScript, so a plain fetch won't see
the cars. Run with `--debug`; if a page shows "0 JSON-LD blocks," open it and
check whether listings appear in *View Source*. If not, either rely on the other
dealer, or swap `fetch()` for a headless browser (Playwright: `pip install
playwright && playwright install chromium`, then fetch with `page.content()`).
Scraping is inherently fragile — a dealer redesign may need a small tweak.

Be reasonable: a couple runs a week, normal User-Agent. Check each site's terms;
this is for personal shopping, not redistribution.
