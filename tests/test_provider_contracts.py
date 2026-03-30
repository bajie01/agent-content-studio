import json
from pathlib import Path

from app.agents.search_providers import (
    BaiduQianfanProvider,
    BochaProvider,
    JuheNewsProvider,
    TianAPIProvider,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "providers"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_baidu_qianfan_contract_parse() -> None:
    provider = BaiduQianfanProvider()
    payload = _load_fixture("baidu_qianfan_chat.json")
    materials = provider._extract_materials("AIGC 内容创作", payload, 5)

    assert len(materials) == 2
    assert materials[0].provider == "baidu_qianfan"
    assert materials[0].url.startswith("https://")
    assert materials[0].title


def test_bocha_contract_parse() -> None:
    provider = BochaProvider()
    payload = _load_fixture("bocha_web_search.json")
    materials = provider._extract_materials("AIGC 内容创作", payload, 5)

    assert len(materials) == 2
    assert all(m.provider == "bocha" for m in materials)
    assert materials[0].domain == "example.com"


def test_tianapi_contract_parse() -> None:
    provider = TianAPIProvider()
    payload = _load_fixture("tianapi_generalnews.json")
    materials = provider._extract_materials("AIGC 内容创作", payload, 5)

    assert len(materials) == 2
    assert all(m.provider == "tianapi" for m in materials)
    assert all(m.published_at for m in materials)


def test_juhe_contract_parse_with_query_filter() -> None:
    provider = JuheNewsProvider()
    payload = _load_fixture("juhe_toutiao.json")
    materials = provider._extract_materials("AIGC", payload, 10)

    assert len(materials) == 1
    assert materials[0].provider == "juhe"
    assert "AIGC" in materials[0].title


def test_contract_schema_mismatch_returns_empty() -> None:
    bocha = BochaProvider()
    tian = TianAPIProvider()
    juhe = JuheNewsProvider()

    assert bocha._extract_materials("AIGC", {"unexpected": 1}, 3) == []
    assert tian._extract_materials("AIGC", {"result": {"newslist": "bad"}}, 3) == []
    assert juhe._extract_materials("AIGC", {"result": {"data": "bad"}}, 3) == []

