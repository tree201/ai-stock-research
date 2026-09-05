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
# 同一名录的官方中文版（繁体），列结构与英文版一致：代码/公司名称/分类/子分类/面值
HKEX_SECURITIES_URL_ZH = (
    "https://www.hkex.com.hk/chi/services/trading/securities/"
    "securitieslists/ListOfSecurities_c.xlsx"
)
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

try:  # 可选依赖：缺失时保留 HKEX 官方繁体名
    from zhconv import convert as _zh_convert

    def to_simplified(text: str) -> str:
        return _zh_convert(text, "zh-cn")
except ImportError:  # pragma: no cover - 环境未装 zhconv 时兜底
    def to_simplified(text: str) -> str:
        return text


def _catalog_symbol(symbol: str) -> str:
    digits = "".join(ch for ch in symbol if ch.isdigit())
    return digits.zfill(5) if digits else ""


def _read_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    shared_root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    return ["".join(text.text or "" for text in item.iter(f"{{{_NS['m']}}}t")) for item in shared_root.findall("m:si", _NS)]


@lru_cache(maxsize=1)
def list_hk_company_names_zh() -> dict[str, str]:
    """Fetch the official HKEX Chinese catalog and return symbol -> 简体中文名.

    Names are published in Traditional Chinese; zhconv converts them to
    Simplified when available. Network failures raise — callers should wrap
    in try/except when the lookup is best-effort.
    """
    request = Request(HKEX_SECURITIES_URL_ZH, headers={"User-Agent": "AI Stock Research/0.1"})
    with urlopen(request, timeout=15) as response:
        workbook = zipfile.ZipFile(io.BytesIO(response.read()))
    shared = _read_shared_strings(workbook)
    sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
    names: dict[str, str] = {}
    for row in sheet.findall(".//m:row", _NS)[3:]:
        values: list[str] = []
        for cell in row.findall("m:c", _NS):
            value = cell.find("m:v", _NS)
            text = value.text if value is not None else ""
            if cell.attrib.get("t") == "s" and text:
                text = shared[int(text)]
            values.append(" ".join((text or "").split()))
        if len(values) < 2:
            continue
        symbol = _catalog_symbol(values[0])
        name = values[1]
        if symbol and name:
            names[symbol] = to_simplified(name)
    return names


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
    # 附带官方简体中文名（best-effort：中文名录拉取失败不影响英文目录）
    try:
        names_zh = list_hk_company_names_zh()
        for entry in companies:
            zh = names_zh.get(entry["symbol"])
            if zh:
                entry["name_zh"] = zh
    except OSError:
        pass
    return tuple(companies)
