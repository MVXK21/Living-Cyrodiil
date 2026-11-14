"""Downloads UESP pages for the lore pass (docs/phase5-lore-pass-plan.md).

The pages are only research material: the lore entries are written from
them in our own words (CC BY-SA, credited in each lore file). They're cached
in companion/cache/uesp/, which isn't committed.

Usage (from the project folder):
    companion\\.venv\\Scripts\\python.exe tools\\fetch_uesp.py "Oblivion:Chorrol" "Oblivion:Chorrol People"
    ... --wikitext       the raw wiki source (keeps tables, which plain text drops)
    ... --list PAGE      print the Oblivion:/Lore: pages that PAGE links to

Requests go one at a time with a pause between them, as UESP asks of bots.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://en.uesp.net/w/api.php"
CACHE = Path(__file__).resolve().parent.parent / "companion" / "cache" / "uesp"
AGENT = "LivingCyrodiil-lore-builder/0.1 (Oblivion mod; lore research, cached, one request at a time)"
PAUSE = 1.5      # seconds between requests


def _get(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    time.sleep(PAUSE)
    return data


def _file(title: str, wikitext: bool, intro: bool = False) -> Path:
    name = re.sub(r'[\\/:*?"<>|]', "_", title)
    return CACHE / f"{name}{'.wiki' if wikitext else ''}{'.intro' if intro else ''}.txt"


def fetch(title: str, wikitext: bool = False, refresh: bool = False, intro: bool = False) -> str:
    """The page's text (cached). Follows redirects. "" if the page doesn't exist.
    `intro`: only the page's opening section (character pages, for bios)."""
    path = _file(title, wikitext, intro)
    if path.is_file() and not refresh:
        return path.read_text(encoding="utf-8")
    if wikitext:
        data = _get({"action": "parse", "page": title, "prop": "wikitext", "redirects": 1})
        text = data.get("parse", {}).get("wikitext", "")
    else:
        params = {"action": "query", "prop": "extracts", "explaintext": 1, "titles": title, "redirects": 1}
        if intro:
            params["exintro"] = 1
        data = _get(params)
        pages = data.get("query", {}).get("pages", [])
        text = pages[0].get("extract", "") if pages and not pages[0].get("missing") else ""
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title} (fetched {time.strftime('%Y-%m-%d')})\n{text}", encoding="utf-8")
    return path.read_text(encoding="utf-8")


def links(title: str) -> list[str]:
    """Oblivion: and Lore: pages linked from `title`."""
    out, cont = [], {}
    while True:
        data = _get({"action": "query", "prop": "links", "titles": title, "pllimit": "max", "redirects": 1, **cont})
        for page in data.get("query", {}).get("pages", []):
            out += [l["title"] for l in page.get("links", []) if l["title"].startswith(("Oblivion:", "Lore:"))]
        if "continue" not in data:
            return out
        cont = data["continue"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("titles", nargs="*")
    ap.add_argument("--wikitext", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--intro", action="store_true", help="only the opening section")
    ap.add_argument("--list", metavar="PAGE")
    args = ap.parse_args()
    if args.list:
        print("\n".join(links(args.list)))
        return 0
    for t in args.titles:
        text = fetch(t, args.wikitext, args.refresh, args.intro)
        print(f"{t}: {len(text)} chars -> {_file(t, args.wikitext, args.intro)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
