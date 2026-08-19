# MDX Search — new Acura MDX around Yuba City

Scrapes the nearest Acura dealers (Niello Acura, Elk Grove Acura) for new MDX
listings and records price, MSRP, discount, and trim. No API key, no card.

Dealer sites block plain HTTP requests (403), so it uses a real headless
Chromium (Playwright) to load each page, then reads the structured schema.org
JSON-LD.

## Run locally
```bash
pip install -r requirements.txt
python -m playwright install chromium
python mdx_search.py
```
Outputs a table plus `mdx_listings_<date>.csv` (every car) and appends a summary
row to `mdx_history.csv` (the price trend).

Flags: `--debug` (diagnostics), `--json`, `--requests` (plain HTTP — expect 403).

## Run it on GitHub (automatic, twice a week)

The scraper needs a real browser AND an IP the dealer won't block. GitHub's cloud
runners use datacenter IPs that these dealers often 403 even with a real browser,
so there are two options — the workflow ships set to the reliable one.

### Path B — self-hosted runner on SGN-PC29 (default, reliable)
GitHub still owns the schedule, logs, history, and the "Run workflow" button; the
job just executes on your always-on PC, over your home IP.

1. In the repo: **Settings → Actions → Runners → New self-hosted runner →
   Windows**. GitHub shows copy-paste commands with a one-time token. Run them in
   an **Administrator PowerShell** on SGN-PC29. They download the runner and then
   `./config.cmd ...` — when it asks **"Run as service?"**, answer **Y** so it
   works while you're logged out.
2. Make sure Python and Git are on PATH for the service account (they are for you;
   the service runs as the same box). Chromium installs on first workflow run.
3. Push this repo, then **Actions → Scrape MDX → Run workflow**. The runner picks
   it up; `mdx_history.csv` appears when it finishes. After that it runs Mon & Thu.

Self-hosted runners are safe on **private** repos (keep this repo private).

### Path A — GitHub cloud runner (zero local footprint, may get blocked)
Edit `.github/workflows/scrape.yml` and change `runs-on: self-hosted` to
`runs-on: ubuntu-latest`. Commit and run it. If the log shows `0 found` with
403 / 0-byte pages, the dealer WAF blocked the datacenter IP — switch back to
Path B.

## Add dealers
Edit the `DEALERS` list at the top of `mdx_search.py`.

## Notes
- Be reasonable: twice a week, real browser, normal cadence. Check each site's
  terms; this is for personal shopping, not redistribution.
- Scraping is fragile — a dealer redesign can require a small parser tweak. If a
  run shows `0 found`, run `python mdx_search.py --debug` and check whether pages
  loaded and how many JSON-LD blocks they exposed.
