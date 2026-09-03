"""HKEX listed-company catalog used by the new-research picker."""

from __future__ import annotations

from functools import lru_cache
import io
import zipfile
from typing import Any
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

HKEX_SECURITIES_URL = (
    "https://www.hkex.com.hk/eng/services/trading/securities/"
    "securitieslists/ListOfSecurities.xlsx"
)
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@lru_cache(maxsize=1)
def list_hk_companies() -> tuple[dict[str, Any], ...]:
    """Fetch and parse the current HKEX listed equity/REIT catalog.

    The result is cached for the lifetime of the web process so opening the
    picker does not repeatedly contact HKEX. Only company-like securities are
    included; warrants, derivatives and ETFs are intentionally omitted.
    """
    request = Request(HKEX_SECURITIES_URL, headers={"User-Agent": "AI Stock Research/0.1"})
    with urlopen(request, timeout=8) as response:
        workbook = zipfile.ZipFile(io.BytesIO(response.read()))
    shared_root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    shared = ["".join(text.text or "" for text in item.iter(f"{{{_NS['m']}}}t")) for item in shared_root.findall("m:si", _NS)]
    sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))

    included_categories = {
        "Equity Securities (Main Board)",
        "Equity Securities (GEM)",
        "Investment Companies",
        "Depositary Receipts",
    }
    companies: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    for row in sheet.findall(".//m:row", _NS)[3:]:
        values: list[str] = []
        for cell in row.findall("m:c", _NS):
            value = cell.find("m:v", _NS)
            text = value.text if value is not None else ""
            if cell.attrib.get("t") == "s" and text:
                text = shared[int(text)]
            values.append(" ".join((text or "").split()))
        if len(values) < 4:
            continue
        category, subcategory = values[2], values[3]
        if not ((category == "Equity" and subcategory in included_categories) or category == "Real Estate Investment Trusts"):
            continue
        symbol = values[0].zfill(5)
        if not symbol or symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        companies.append({"symbol": symbol, "name": values[1], "market": "HK"})
    return tuple(companies)
