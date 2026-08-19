#!/usr/bin/env python3
"""
mdx_search.py — find new Acura MDX listings around Yuba City, CA.

Dealer sites block bare HTTP (403). Each fetch tries a full-browser-header
request first, then falls back to a real headless Chromium (Playwright) — the
fingerprint that gets past the WAF. Run on your home IP / a self-hosted runner.

  * Elk Grove Acura — sanctioned /llm/inventory/ endpoint (clean text listings).
  * Niello Acura     — Dealer.com; read from schema.org JSON-LD.

Setup:
    pip install -r requirements.txt
    python -m playwright install chromium

Usage:
    python mdx_search.py
    python mdx_search.py --debug     # diagnostics + writes debug_<dealer>.json
    python mdx_search.py --json
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
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="126", "Google Chrome";v="126", "Not:A-Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}
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


# --- fetcher: rich-header request, then Chromium fallback --------------------
class Fetcher:
    def __init__(self, debug=False):
        self.debug = debug
        self._pw = self._browser = self._page = None

    def _ensure_browser(self):
        if self._page:
            return
        from playwright.sync_api import sync_playwright  # raises ImportError if absent
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = self._browser.new_context(user_agent=UA, locale="en-US",
                                        viewport={"width": 1366, "height": 900})
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        self._page = ctx.new_page()

    def get(self, url):
        try:
            r = requests.get(url, headers=HEADERS, timeout=45)
            r.raise_for_status()
            if self.debug:
                print(f"    requests ok ({len(r.text)} b)", file=sys.stderr)
            return r.text
        except Exception as e:  # noqa: BLE001
            if self.debug:
                print(f"    requests failed ({e}); rendering with Chromium", file=sys.stderr)
        try:
            self._ensure_browser()
            self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
            self._page.wait_for_timeout(3500)
            content = self._page.content()
            if self.debug:
                print(f"    rendered ({len(content)} b)", file=sys.stderr)
            return content
        except ImportError:
            print(f"  Playwright not installed — can't bypass 403 for {url}\n"
                  f"    pip install playwright && python -m playwright install chromium",
                  file=sys.stderr)
            return ""
        except Exception as e:  # noqa: BLE001
            if self.debug:
                print(f"    render failed ({e})", file=sys.stderr)
            return ""

    def close(self):
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass


# --- DealerInspire /llm/ endpoint --------------------------------------------
LLM_BLOCK = re.compile(
    r"-\s*\[(?P<title>[^\]]+)\]\((?P<url>https?://[^)]+)\)\s*\n"
    r"\s*(?P<cond>New|Certified Used|Used)\b[^\n]*\n"
    r"\s*(?P<price>Call for price|\$[\d,]+)[^\n]*\n"
    r"\s*VIN:\s*(?P<vin>[A-Za-z0-9]+)",
    re.MULTILINE,
)


def parse_llm(dealer, fetcher, debug=False):
    found, seen = [], set()
    page, total_pages = 1, 1
    while page <= total_pages and page <= 8:
        url = f"{dealer['base']}?make=Acura&limit=100&page={page}"
        if debug:
            print(f"  GET {url}", file=sys.stderr)
        raw = fetcher.get(url)
        if not raw:
            break
        text = BeautifulSoup(raw, "html.parser").get_text("\n")
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


# --- Dealer.com JSON-LD ------------------------------------------------------
def walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def jsonld_nodes(text, debug=False, dump_path=None):
    soup = BeautifulSoup(text, "html.parser")
    blocks = soup.find_all("script", attrs={"type": "application/ld+json"})
    if debug:
        print(f"    {len(blocks)} JSON-LD block(s)", file=sys.stderr)
    if dump_path:
        with open(dump_path, "w", encoding="utf-8") as f:
            f.write("\n\n---\n\n".join(
                html.unescape((b.string or b.get_text() or "")) for b in blocks))
        print(f"    wrote {dump_path}", file=sys.stderr)
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


def is_vehicle(node):
    t = str(node.get("@type", "")).lower()
    return t in ("car", "vehicle", "product", "individualproduct") or "vin" in node


def matches_mdx(node):
    hay = " ".join(str(node.get(k, "")) for k in ("name", "model", "description", "sku"))
    if isinstance(node.get("model"), dict):
        hay += " " + str(node["model"].get("name", ""))
    return MODEL.lower() in hay.lower()


def parse_jsonld(dealer, fetcher, debug=False):
    found, seen = [], set()
    for i, url in enumerate(dealer["urls"]):
        if debug:
            print(f"  GET {url}", file=sys.stderr)
        raw = fetcher.get(url)
        if not raw:
            continue
        dump = f"debug_{dealer['name'].split()[0].lower()}_{i}.json" if debug else None
        for node in jsonld_nodes(raw, debug=debug, dump_path=dump):
            if not isinstance(node, dict) or not is_vehicle(node) or not matches_mdx(node):
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
            found.append({"price": sale, "msrp": msrp, "discount": disc,
                          "name": name_of(node), "vin": vin, "url": node.get("url", ""),
                          "dealer": dealer["name"], "city": dealer["city"]})
    return found


# --- driver -------------------------------------------------------------------
def search(debug=False):
    fetcher = Fetcher(debug)
    all_rows, per_dealer = [], []
    try:
        for d in DEALERS:
            rows = (parse_llm(d, fetcher, debug) if d["kind"] == "dealerinspire_llm"
                    else parse_jsonld(d, fetcher, debug))
            per_dealer.append((d, len(rows)))
            all_rows.extend(rows)
    finally:
        fetcher.close()
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
