#!/usr/bin/env python3
"""
mdx_search.py — find new Acura MDX listings around Yuba City, CA.

Source-aware per dealer:
  * DealerInspire dealers (e.g. Elk Grove Acura) expose a sanctioned, JS-free
    /llm/inventory/ endpoint. Parsed with plain HTTP — no browser, works from
    anywhere including GitHub cloud runners.
  * Dealer.com dealers (e.g. Niello Acura) block plain requests and render
    inventory with JavaScript, so those are loaded with a real headless
    Chromium (Playwright) and read from schema.org JSON-LD.

If Playwright isn't installed (or a render fails), Dealer.com dealers are
skipped with a warning and the /llm/ dealers still return results.

Setup:
    pip install -r requirements.txt
    python -m playwright install chromium   # only needed for Dealer.com dealers

Usage:
    python mdx_search.py           # search around Yuba City
    python mdx_search.py --json
    python mdx_search.py --debug
"""
import os
import re
import sys
import csv
import json
import html
import argparse
import statistics
import datetime

import requests
from bs4 import BeautifulSoup

# --- Dealers near Yuba City ---------------------------------------------------
DEALERS = [
    {
        "name": "Elk Grove Acura",
        "city": "Elk Grove, CA",
        "kind": "dealerinspire_llm",
        "base": "https://www.elkgroveacura.com/llm/inventory/",
    },
    {
        "name": "Niello Acura",
        "city": "Roseville, CA",
        "kind": "jsonld_render",
        "urls": ["https://acura.niello.com/new-inventory/index.htm?model=MDX"],
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


def fetch_requests(url, debug=False):
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept": "text/html"},
                         timeout=45)
        r.raise_for_status()
        return r.text
    except Exception as e:  # noqa: BLE001
        if debug:
            print(f"    requests failed: {e}", file=sys.stderr)
        return ""


# --- DealerInspire /llm/ endpoint (plain HTTP, no browser) --------------------
LLM_BLOCK = re.compile(
    r"-\s*\[(?P<title>[^\]]+)\]\((?P<url>https?://[^)]+)\)\s*\n"
    r"\s*(?P<cond>New|Certified Used|Used)\b[^\n]*\n"
    r"\s*(?P<price>Call for price|\$[\d,]+)[^\n]*\n"
    r"\s*VIN:\s*(?P<vin>[A-Za-z0-9]+)",
    re.MULTILINE,
)


def parse_llm(dealer, debug=False):
    found, seen = [], set()
    page, total_pages = 1, 1
    while page <= total_pages and page <= 8:
        url = f"{dealer['base']}?make=Acura&limit=100&page={page}"
        if debug:
            print(f"  GET {url}", file=sys.stderr)
        text = fetch_requests(url, debug)
        if not text:
            break
        m = re.search(r"Page\s+\d+\s+of\s+(\d+)", text)
        if m:
            total_pages = int(m.group(1))
        n_here = 0
        for mo in LLM_BLOCK.finditer(text):
            title = mo.group("title").strip()
            if mo.group("cond") != "New" or "MDX" not in title or "Acura" not in title:
                continue
            vin = mo.group("vin")
            if vin in seen:
                continue
            seen.add(vin)
            price = None if mo.group("price").startswith("Call") else num(mo.group("price"))
            found.append({"price": price, "msrp": None, "discount": None,
                          "name": title, "vin": vin, "url": mo.group("url"),
                          "dealer": dealer["name"], "city": dealer["city"]})
            n_here += 1
        if debug:
            print(f"    page {page}/{total_pages}: {n_here} new MDX", file=sys.stderr)
        page += 1
    return found


# --- Dealer.com via Playwright + JSON-LD --------------------------------------
def render_all(urls, debug=False):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  (Playwright not installed — skipping Dealer.com dealers. "
              "Run `pip install playwright && python -m playwright install chromium` "
              "to include them.)", file=sys.stderr)
        return {}
    out = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
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
                    page.wait_for_timeout(3500)
                    out[url] = page.content()
                    if debug:
                        print(f"    got {len(out[url])} bytes", file=sys.stderr)
                except Exception as e:  # noqa: BLE001
                    if debug:
                        print(f"    render failed: {e}", file=sys.stderr)
                    out[url] = ""
            browser.close()
    except Exception as e:  # noqa: BLE001
        print(f"  (browser error: {e})", file=sys.stderr)
    return out


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


def matches_mdx(node):
    hay = " ".join(str(node.get(k, "")) for k in ("name", "model", "description", "sku"))
    if isinstance(node.get("model"), dict):
        hay += " " + str(node["model"].get("name", ""))
    return MODEL.lower() in hay.lower()


def parse_jsonld(dealer, htmls, debug=False):
    found, seen = [], set()
    for url in dealer["urls"]:
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
            u = node.get("url") or ""
            found.append({"price": sale, "msrp": msrp, "discount": disc,
                          "name": name_of(node), "vin": vin, "url": u,
                          "dealer": dealer["name"], "city": dealer["city"]})
    return found


# --- driver -------------------------------------------------------------------
def search(debug=False):
    render_urls = [u for d in DEALERS if d["kind"] == "jsonld_render" for u in d["urls"]]
    htmls = render_all(render_urls, debug) if render_urls else {}

    all_rows, per_dealer = [], []
    for d in DEALERS:
        if d["kind"] == "dealerinspire_llm":
            rows = parse_llm(d, debug)
        else:
            rows = parse_jsonld(d, htmls, debug)
        per_dealer.append((d, len(rows)))
        all_rows.extend(rows)
    return all_rows, per_dealer


def money(n):
    return f"${n:,.0f}" if isinstance(n, (int, float)) else "—"


def main():
    ap = argparse.ArgumentParser(description="Find new Acura MDX listings around Yuba City.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--no-csv", action="store_true")
    args = ap.parse_args()

    rows, per_dealer = search(debug=args.debug)
    rows.sort(key=lambda r: (r["price"] is None, r["price"] or 0))

    if args.json:
        print(json.dumps(rows, indent=2))
        return

    today = datetime.date.today().isoformat()
    print(f"\nAcura MDX — new, around Yuba City · {today}\n")
    for d, n in per_dealer:
        print(f"  {'✓' if n else '·'} {d['name']:<18} {d['city']:<16} {n} found")
    print()

    if not rows:
        print("No listings parsed. Run with --debug to see what each source returned.\n")
        return

    print(f"  {'PRICE':>9}  {'MSRP':>9}  {'SAVE':>7}  {'TRIM / NAME':<36} DEALER")
    print("  " + "-" * 80)
    for r in rows:
        save = money(r["discount"]) if r["discount"] else ""
        name = (r["name"] or "").replace("New ", "")[:36]
        print(f"  {money(r['price']):>9}  {money(r['msrp']):>9}  {save:>7}  {name:<36} {r['dealer']}")

    prices = [r["price"] for r in rows if r["price"]]
    discs = [r["discount"] for r in rows if r["discount"]]
    print("  " + "-" * 80)
    line = f"  {len(rows)} listings"
    if prices:
        line += f" · median {money(statistics.median(prices))} · low {money(min(prices))}"
    if discs:
        line += f" · best save {money(max(discs))}"
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
