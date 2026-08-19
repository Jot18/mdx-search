# MDX Search — new Acura MDX around Yuba City

Finds new Acura MDX listings at the nearest dealers and records price + trim.
No API key, no card. Source-aware per dealer:

- **Elk Grove Acura** — uses their sanctioned `/llm/inventory/` endpoint (built
  for AI assistants, documented at `/llms.txt`). Plain HTTP, no JavaScript, no
  blocking. **Works anywhere, including GitHub cloud runners.**
- **Niello Acura** — Dealer.com; blocks plain requests and renders inventory
  with JavaScript, so it's loaded with a real headless Chromium (Playwright) and
  read from schema.org JSON-LD. Needs a residential IP to avoid the 403, so it
  only works locally or on a self-hosted runner.

If Playwright isn't installed or a render fails, Niello is skipped with a warning
and Elk Grove still returns results.

## Run locally
```bash
pip install -r requirements.txt
python -m playwright install chromium   # only needed for Niello
python mdx_search.py
```
Writes `mdx_listings_<date>.csv` (every car) and appends a summary row to
`mdx_history.csv` (the price trend). Flags: `--debug`, `--json`.

## Run on GitHub (automatic, twice a week)

The workflow ships set to `runs-on: self-hosted` so it can do **both** dealers.

### Reliable path — self-hosted runner on SGN-PC29 (both dealers)
GitHub owns the schedule, logs, history, and Run-workflow button; the job just
executes on your always-on PC (home IP + Chromium).

1. Repo → **Settings → Actions → Runners → New self-hosted runner → Windows**.
   Run the shown commands (with the one-time token) in an **Administrator
   PowerShell** on SGN-PC29. When `config.cmd` asks **"Run as service?"**, say
   **Y** so it runs while you're logged out.
2. Push this repo, then **Actions → Scrape MDX → Run workflow**. `mdx_history.csv`
   appears when it finishes; after that it runs Mon & Thu.

Self-hosted runners are safe on **private** repos — keep this repo private.

### Zero-local path — GitHub cloud runner (Elk Grove only)
Edit `.github/workflows/scrape.yml`, change `runs-on: self-hosted` to
`runs-on: ubuntu-latest`. Elk Grove's `/llm/` endpoint returns fine from the
cloud; Niello will be skipped (datacenter IP + JS). You still get a real,
tracked MDX price history — just from one dealer.

## Add dealers
Edit the `DEALERS` list in `mdx_search.py`:
- DealerInspire site? Check `https://THEIRSITE/llms.txt`. If it lists a
  `/llm/inventory/` endpoint, add `{"kind":"dealerinspire_llm","base":"…/llm/inventory/"}`.
- Dealer.com or other JS site? Add `{"kind":"jsonld_render","urls":[…]}` pointing
  at the New/MDX inventory page.

## Notes
- Elk Grove's endpoint is meant for this. Niello is real scraping — be light
  (twice a week) and check the site's terms; personal use, not redistribution.
- Niello currently reports price with no MSRP (Dealer.com quirk), so its discount
  column is blank — sanity-check its prices against the site the first time.
