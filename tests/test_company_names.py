"""公司中文名（HKEX 官方名录 + 显示偏好）测试。"""

import io
import json
import zipfile
from tempfile import TemporaryDirectory
from datetime import date
import os
import unittest
from unittest import mock

from stock_research import hk_companies, service
from stock_research.storage import SQLiteStore

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _xlsx(rows: list[list[str]]) -> bytes:
    """构造最小 xlsx（sharedStrings + sheet1），行结构与 HKEX 名录一致。"""
    strings: list[str] = []

    def sid(text: str) -> str:
        if text not in strings:
            strings.append(text)
        return str(strings.index(text))

    body: list[str] = []
    for index, row in enumerate(rows, start=1):
        cells = "".join(
            f'<c t="s"><v>{sid(value)}</v></c>' for value in row
        )
        body.append(f'<row r="{index}">{cells}</row>')
    sheet = (
        f'<?xml version="1.0"?><worksheet xmlns="{NS}">{"".join(body)}</worksheet>'
    )
    shared = (
        "<?xml version='1.0'?><sst xmlns='"
        + NS
        + "'>"
        + "".join(f"<si><t>{value}</t></si>" for value in strings)
        + "</sst>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


class ZhCatalogTests(unittest.TestCase):
    def test_to_simplified(self) -> None:
        self.assertEqual(hk_companies.to_simplified("長和"), "长和")
        self.assertEqual(hk_companies.to_simplified("中電控股"), "中电控股")

    def test_zh_catalog_parse_and_convert(self) -> None:
        data = _xlsx([
            ["SECURITY_CODE", "SECURITY_NAME", "CATEGORY", "SUBCATEGORY", "PAR"],
            [], [], [],
            ["00001", "長和", "股本", "股本證券(主板)", "500"],
            ["700", "騰訊控股", "股本", "股本證券(主板)", "500"],
            ["", "", "", "", ""],
        ])
        fake_response = mock.mock_open(read_data=data).return_value
        fake_response.__enter__.return_value = fake_response
        with mock.patch.object(hk_companies, "urlopen", return_value=fake_response):
            hk_companies.list_hk_company_names_zh.cache_clear()
            names = hk_companies.list_hk_company_names_zh()
        self.assertEqual(names["00001"], "长和")
        self.assertEqual(names["00700"], "腾讯控股")  # 数字补零归一
        hk_companies.list_hk_company_names_zh.cache_clear()


class DisplayNamePrefTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"AI_STOCK_DB": f"{self._dir.name}/r.sqlite3"}, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._dir.cleanup()

    def test_default_pref_is_zh_and_status_exposes_it(self) -> None:
        self.assertEqual(service.company_name_display(), "zh")
        self.assertEqual(service.provider_status()["company_name_display"], "zh")

    def test_set_pref_roundtrip_and_validation(self) -> None:
        result = service.set_display_preference_payload({"display": "en"})
        self.assertEqual(result["company_name_display"], "en")
        self.assertEqual(service.company_name_display(), "en")
        with self.assertRaises(ValueError):
            service.set_display_preference_payload({"display": "kr"})


class CreateProjectNameZhTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"AI_STOCK_DB": f"{self._dir.name}/r.sqlite3"}, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._dir.cleanup()

    def test_create_project_enriches_name_zh(self) -> None:
        with mock.patch.object(service, "list_hk_company_names_zh", return_value={"00700": "腾讯控股"}):
            result = service.create_project_with_session({"name": "Tencent", "symbol": "0700", "market": "HK"})
        self.assertEqual(result["project"]["name_zh"], "腾讯控股")
        store = SQLiteStore(f"{self._dir.name}/r.sqlite3")
        try:
            rows = store.list_companies()
            self.assertEqual(rows[0]["name_zh"], "腾讯控股")
        finally:
            store.close()

    def test_existing_project_keeps_name_zh_on_recreate(self) -> None:
        with mock.patch.object(service, "list_hk_company_names_zh", return_value={"00700": "腾讯控股"}):
            service.create_project_with_session({"name": "Tencent", "symbol": "00700", "market": "HK"})
            again = service.create_project_with_session({"name": "Tencent", "symbol": "00700", "market": "HK"})
        self.assertEqual(again["project"]["name_zh"], "腾讯控股")


class BackfillTests(unittest.TestCase):
    def test_backfill_fills_only_missing(self) -> None:
        with TemporaryDirectory() as directory:
            store = SQLiteStore(f"{directory}/r.sqlite3")
            try:
                store.backfill_name_zh({"00700": "腾讯控股"})  # 空库：0 行
                from stock_research.domain import ResearchProject
                from datetime import datetime, timezone
                from uuid import uuid4

                now = datetime.now(timezone.utc)
                plain = ResearchProject(user_id=uuid4(), company_id=uuid4(), symbol="09988", name="Alibaba", market="HK")
                store.save_project(plain)
                count = store.backfill_name_zh({"09988": "阿里巴巴集团", "00700": "腾讯控股"})
                self.assertEqual(count, 1)
                self.assertEqual(store.load_project(plain.id).name_zh, "阿里巴巴集团")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
