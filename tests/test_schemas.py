import pytest
from pydantic import ValidationError

from app.schemas import GenerateRequest


def test_generate_request_accepts_new_platform_field() -> None:
    req = GenerateRequest(topic="AIGC内容创作", platform="ZHIHU")
    assert req.platform == "zhihu"
    assert req.platforms == ["zhihu"]


def test_generate_request_accepts_legacy_platforms_field() -> None:
    req = GenerateRequest(topic="AIGC内容创作", platforms=["XIAOHONGSHU"])
    assert req.platform == "xiaohongshu"
    assert req.platforms == ["xiaohongshu"]


def test_generate_request_rejects_invalid_platform() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest(topic="AIGC内容创作", platform="weibo")


def test_generate_request_rejects_multiple_platforms() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest(topic="AIGC内容创作", platforms=["zhihu", "baijiahao"])

