#!/usr/bin/env python3
"""
Fetches and parses today's menu for the two Berlin mensas, using
coordinate-based column extraction calibrated against the real PDF
layout (columns are evenly spaced ~141.7pt apart, starting at x=110.6
on a standard page — verified against an actual downloaded PDF).

Requirements:
    pip3 install requests pdfplumber

Usage:
    python3 mensa_menu.py
"""

import json
import re
import sys
from datetime import datetime
from io import BytesIO

import requests
import pdfplumber

MENSAS = {
    "TU Hardenbergstraße": "https://www.stw.berlin/assets/speiseplaene/321/aktuelle_woche_en.pdf",
    "HU Süd": "https://www.stw.berlin/assets/speiseplaene/367/aktuelle_woche_en.pdf",
}

DAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]
COL_START_X = 110.6
COL_SPACING = 141.7
N_COLS = 5
PRICE_PAT = re.compile(r"\d,\d{2}")


def col_for_x(x0: float) -> int:
    idx = round((x0 - COL_START_X) / COL_SPACING)
    return max(0, min(N_COLS - 1, idx))


def parse_page(page):
    """Return {day_de: {category: [dish_text, ...]}} for one page."""
    words = page.extract_words()
    if not words:
        return {}

    cat_words = [w for w in words if w["x0"] < 90 and w["top"] > 100]
    cat_rows = sorted([(w["top"], w["text"]) for w in cat_words])

    body_words = [w for w in words if w["top"] > 100 and w["x0"] >= 90]

    result = {d: {} for d in DAYS_DE}
    for day_idx, day in enumerate(DAYS_DE):
        col_words = [w for w in body_words if col_for_x(w["x0"]) == day_idx]
        col_words.sort(key=lambda w: (round(w["top"], 1), w["x0"]))

        for w in col_words:
            best_cat, best_dist = None, None
            for cat_top, cat_name in cat_rows:
                if cat_top <= w["top"] + 5:
                    dist = w["top"] - cat_top
                    if best_dist is None or dist < best_dist:
                        best_dist, best_cat = dist, cat_name
            if best_cat is None:
                continue
            result[day].setdefault(best_cat, []).append(w["text"])

    for day in result:
        for cat in result[day]:
            text = " ".join(result[day][cat]).split("Beschäftigte")[0]
            tokens = text.split()
            dishes, current, price_count = [], [], 0
            for tok in tokens:
                current.append(tok)
                if PRICE_PAT.fullmatch(tok.strip("€|")):
                    price_count += 1
                if price_count == 3:
                    dishes.append(" ".join(current))
                    current, price_count = [], 0
            if current:
                dishes.append(" ".join(current))
            result[day][cat] = [d for d in dishes if len(d) > 3]

    return result


def fetch_menu(url: str) -> dict:
    resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    full = {d: {} for d in DAYS_DE}
    with pdfplumber.open(BytesIO(resp.content)) as pdf:
        for page in pdf.pages[:5]:  # first 5 pages hold the menu; last is the legend
            page_result = parse_page(page)
            for day, cats in page_result.items():
                for cat, dishes in cats.items():
                    full[day].setdefault(cat, []).extend(dishes)
    return full


def main():
    weekday_idx = datetime.now().weekday()  # Monday=0 ... Sunday=6
    today_de = DAYS_DE[weekday_idx] if weekday_idx < 5 else None

    output = {"date": datetime.now().strftime("%Y-%m-%d"), "mensas": {}}

    for name, url in MENSAS.items():
        print(f"Fetching {name}...", file=sys.stderr)
        try:
            full_week = fetch_menu(url)
        except Exception as e:
            output["mensas"][name] = {"error": str(e)}
            continue

        if today_de is None:
            output["mensas"][name] = {"status": "weekend_no_menu"}
            continue

        today_menu = full_week.get(today_de, {})

        # Column-boundary bleed-through means a "closed" day still leaks a
        # handful of stray words from the neighbouring column. Real days
        # have ~90-100+ alphabetic words of length>=3; closed days leak
        # roughly 20-30. Threshold well above the noise floor.
        word_pat = re.compile(r"^[A-Za-zÀ-ÿ]{3,}$")
        real_word_count = sum(
            1
            for dishes in today_menu.values()
            for dish in dishes
            for tok in dish.split()
            if word_pat.match(tok)
        )

        if real_word_count < 50:
            output["mensas"][name] = {"status": "closed_today"}
        else:
            output["mensas"][name] = {"status": "open", "categories": today_menu}

    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
