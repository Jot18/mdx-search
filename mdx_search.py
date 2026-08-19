#!/usr/bin/env python3
"""
mdx_search.py — find new Acura MDX listings around Yuba City, CA.

Dealer sites block bare HTTP requests (403), so by default this renders each
page with a real headless Chromium via Playwright, then parses the structured
schema.org JSON-LD. No API key, no credit card.

Setup:
    pip install -r requirements.txt
    python -m playwright install chromium

Usage:
    python mdx_search.py            # render with Chromium (default)
    python mdx_search.py --requests # plain HTTP (fast, but dealers 403 it)
    python mdx_search.py --json     # machine-readable output
    python mdx_search.py --debug    # show diagnostics per page

Add or change dealers in the DEALERS list below.
"""
import os
import re
import sys
import csv
import json
import html
import time
import argparse
import statistics
import datetime

import requests
from bs4 import BeautifulSoup

# --- Dealers near Yuba City (each URL is tried; results deduped by VIN) --------
DEALERS = [
    {
        "name": "Niello Acura",
        "city": "Roseville, CA",
        "urls": [
            "https://acura.niello.com/sacramento-ca/acura-mdx-inventory.htm",
            "https://acura.niello.com/new-inventory/index.htm?model=MDX",
        ],
    },
    {
        "name": "Elk Grove Acura",
        "city": "Elk Grove, CA",
        "urls": [
            "https://www.elkgroveacura.com/new-vehicles/mdx/",
            "https://www.elkgroveacura.com/new-inventory/index.htm?model=MDX",
        ],
    },
]

MODEL = "MDX"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
MSRP_HINTS = ("msrp", "listprice", "list price", "schema.org/msrp")


def num(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"\d[\d,]*(?:\.\d+)?", v)
        if m:
            try:
                return float(m.group().replace(",", ""))
            except ValueError:
                return None
    return None


# ---- fetching ---------------------------------------------------------------
def fetch_requests(url, debug=False):
    try:
        r = requests.get(url, headers={"User-Agent": UA,
                                       "Accept": "text/html,application/xhtml+xml"},
                         timeout=45)
        r.raise_for_status()
        return r.text
    except Exception as e:  # noqa: BLE001
        if debug:
            print(f"    requests failed: {e}", file=sys.stderr)
        return ""


def render_all(urls, debug=False):
    """Render each URL with a real headless Chromium; return {url: html}."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Run:\n"
              "    pip install playwright\n"
              "    python -m playwright install chromium\n"
              "(or use --requests, though dealers tend to 403 that).", file=sys.stderr)
        sys.exit(1)

    out = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(user_agent=UA, locale="en-US",
                                  viewport={"width": 1366, "height": 900})
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()
        for url in urls:
            try:
                if debug:
                    print(f"  render {url}", file=sys.stderr)
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3500)  # let inventory JS populate
                out[url] = page.content()
                if debug:
                    print(f"    got {len(out[url])} bytes", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                if debug:
                    print(f"    render failed: {e}", file=sys.stderr)
                out[url] = ""
        browser.close()
    return out


# ---- parsing ----------------------------------------------------------------
def walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def jsonld_nodes(text, debug=False):
    soup = BeautifulSoup(text, "html.parser")
    blocks = soup.find_all("script", attrs={"type": "application/ld+json"})
    if debug:
        print(f"    {len(blocks)} JSON-LD block(s)", file=sys.stderr)
    for b in blocks:
        raw = html.unescape((b.string or b.get_text() or "")).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        yield from walk(data)


def prices_from(node):
    sale, msrp, prices = None, None, []
    offers = node.get("offers")
    cands = [offers] if isinstance(offers, dict) else (offers or [])
    for off in cands:
        if not isinstance(off, dict):
            continue
        ptype = str(off.get("priceType", "") or off.get("@type", "")).lower()
        p = off.get("price")
        if p is None and isinstance(off.get("priceSpecification"), dict):
            p = off["priceSpecification"].get("price")
        n = num(p)
        if n is None:
            continue
        if any(h in ptype for h in MSRP_HINTS):
            msrp = n
        else:
            prices.append(n)
    if not prices and num(node.get("price")) is not None:
        prices.append(num(node.get("price")))
    if msrp is None and num(node.get("msrp")) is not None:
        msrp = num(node.get("msrp"))
    if prices:
        sale = min(prices)
    return sale, msrp


def name_of(node):
    n = node.get("name") or ""
    if isinstance(node.get("model"), dict):
        n = n or node["model"].get("name", "")
    return str(n).strip()


def url_of(node):
    if node.get("url"):
        return node["url"]
    offers = node.get("offers")
    if isinstance(offers, dict) and offers.get("url"):
        return offers["url"]
    return ""


def matches_mdx(node):
    hay = " ".join(str(node.get(k, "")) for k in ("name", "model", "description", "sku"))
    if isinstance(node.get("model"), dict):
        hay += " " + str(node["model"].get("name", ""))
    return MODEL.lower() in hay.lower()


def search(use_requests=False, debug=False):
    pairs = [(d, u) for d in DEALERS for u in d["urls"]]
    if use_requests:
        htmls = {u: fetch_requests(u, debug) for _, u in pairs}
    else:
        htmls = render_all([u for _, u in pairs], debug)

    all_rows, per_dealer = [], []
    for d in DEALERS:
        seen, found = set(), []
        for url in d["urls"]:
            text = htmls.get(url, "")
            if not text:
                continue
            for node in jsonld_nodes(text, debug=debug):
                if not isinstance(node, dict) or not matches_mdx(node):
                    continue
                sale, msrp = prices_from(node)
                if sale is None and msrp is None:
                    continue
                vin = node.get("vin") or node.get("sku") or ""
                key = vin or (name_of(node), sale, msrp)
                if key in seen:
                    continue
                seen.add(key)
                disc = (msrp - sale) if (msrp and sale and msrp >= sale) else None
                found.append({
                    "price": sale, "msrp": msrp, "discount": disc,
                    "name": name_of(node), "vin": vin, "url": url_of(node),
                    "dealer": d["name"], "city": d["city"],
                })
        per_dealer.append((d, len(found)))
        all_rows.extend(found)
    return all_rows, per_dealer


def money(n):
    return f"${n:,.0f}" if isinstance(n, (int, float)) else "—"


def main():
    ap = argparse.ArgumentParser(description="Find new Acura MDX listings around Yuba City.")
    ap.add_argument("--json", action="store_true", help="output JSON instead of a table")
    ap.add_argument("--requests", action="store_true", help="use plain HTTP (dealers 403 it)")
    ap.add_argument("--debug", action="store_true", help="show scraping diagnostics")
    ap.add_argument("--no-csv", action="store_true", help="don't write CSV files")
    args = ap.parse_args()

    rows, per_dealer = search(use_requests=args.requests, debug=args.debug)
    rows.sort(key=lambda r: (r["price"] is None, r["price"] or 0))

    if args.json:
        print(json.dumps(rows, indent=2))
        return

    today = datetime.date.today().isoformat()
    print(f"\nAcura MDX — new, around Yuba City · {today}\n")
    for d, n in per_dealer:
        mark = "✓" if n else "·"
        print(f"  {mark} {d['name']:<18} {d['city']:<16} {n} found")
    print()

    if not rows:
        print("No listings parsed. Run `python mdx_search.py --debug` to see whether pages")
        print("were blocked (403 / 0 bytes) or returned no JSON-LD, and share the output.\n")
        return

    print(f"  {'PRICE':>9}  {'MSRP':>9}  {'SAVE':>7}  {'TRIM / NAME':<34} DEALER")
    print("  " + "-" * 78)
    for r in rows:
        save = money(r["discount"]) if r["discount"] else ""
        name = (r["name"] or "").replace("New ", "")[:34]
        print(f"  {money(r['price']):>9}  {money(r['msrp']):>9}  {save:>7}  {name:<34} {r['dealer']}")

    prices = [r["price"] for r in rows if r["price"]]
    discs = [r["discount"] for r in rows if r["discount"]]
    print("  " + "-" * 78)
    line = f"  {len(rows)} listings"
    if prices:
        line += f" · median {money(statistics.median(prices))} · low {money(min(prices))}"
    if discs:
        best = max(discs)
        bpct = next((d["discount"] / d["msrp"] * 100 for d in rows
                     if d["discount"] == best and d["msrp"]), None)
        line += f" · best save {money(best)}"
        if bpct:
            line += f" ({bpct:.1f}%)"
    print(line + "\n")

    if not args.no_csv:
        detail = f"mdx_listings_{today}.csv"
        with open(detail, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["price", "msrp", "discount", "name",
                                              "vin", "dealer", "city", "url"])
            w.writeheader()
            w.writerows(rows)
        hist = "mdx_history.csv"
        new = not os.path.exists(hist)
        with open(hist, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["date", "num_found", "price_min", "price_median",
                            "discount_median_usd", "discount_median_pct"])
            dpct = [d["discount"] / d["msrp"] * 100 for d in rows if d["discount"] and d["msrp"]]
            w.writerow([today, len(rows),
                        f"{min(prices):.0f}" if prices else "",
                        f"{statistics.median(prices):.0f}" if prices else "",
                        f"{statistics.median(discs):.0f}" if discs else "",
                        f"{statistics.median(dpct):.2f}" if dpct else ""])
        print(f"  Saved {detail} and updated {hist}\n")


if __name__ == "__main__":
    main()
