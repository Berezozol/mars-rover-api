"""`xml_safe` and the shared names of the RSS feed (docs/proposals/stage-16-rss.md, sections 4, 6).

`xml_safe(text)` removes the characters that are not allowed in XML 1.0 and keeps everything
else untouched (escaping is the XML serializer's job, not this function's).
"""

import xml.etree.ElementTree as ET

import pytest

from app.api.caching import FEED_CACHE_CONTROL
from app.api.v2.feeds import FEED_MEDIA_TYPE, xml_safe
from app.services import feeds as feed_service

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("a\x00b", "ab", id="nul"),
        pytest.param("a\x0bb", "ab", id="vertical-tab"),
        pytest.param("a\x0cb", "ab", id="form-feed"),
        pytest.param("\x01\x02\x08x\x0e\x1fy", "xy", id="other-c0-controls"),
        pytest.param("a￾b￿c", "abc", id="noncharacters-fffe-ffff"),
        pytest.param("a\ud800b", "ab", id="lone-high-surrogate"),
        pytest.param("a\udc00b", "ab", id="lone-low-surrogate"),
        pytest.param("a\tb\nc\rd", "a\tb\nc\rd", id="tab-lf-cr-are-allowed"),
        pytest.param("Привет, Марс", "Привет, Марс", id="cyrillic"),
        pytest.param("rover \U0001f680 sol", "rover \U0001f680 sol", id="emoji-outside-bmp"),
        pytest.param("ü ñ 火星", "ü ñ 火星", id="non-ascii"),
        pytest.param("", "", id="empty"),
    ],
)
def test_xml_safe_removes_only_forbidden_characters(raw: str, expected: str) -> None:
    assert xml_safe(raw) == expected


def test_xml_safe_keeps_markup_characters_unescaped() -> None:
    text = "<b>&amp; \"quoted\" 'single'</b> ]]>"

    assert xml_safe(text) == text


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("a\x00\x0b\x0c\x1f￾￿b \ud800", id="forbidden"),
        pytest.param('<a href="x">&\'</a> ]]> Привет \U0001f680', id="markup-and-unicode"),
    ],
)
def test_xml_safe_output_is_accepted_by_the_xml_parser(text: str) -> None:
    root = ET.Element("t", attrib={"a": xml_safe(text)})
    root.text = xml_safe(text)

    parsed = ET.fromstring(ET.tostring(root, encoding="utf-8"))

    assert parsed.text == xml_safe(text)
    assert parsed.get("a") == xml_safe(text)


def test_feed_shared_names_match_the_contract() -> None:
    assert FEED_MEDIA_TYPE == "application/rss+xml; charset=utf-8"
    assert FEED_CACHE_CONTROL == "public, max-age=900"
    assert tuple(feed_service.FEED_GROUPS) == ("sol", "photo")
    assert dict(feed_service.FEED_DEFAULT_LIMIT) == {"sol": 20, "photo": 50}
    assert feed_service.FEED_MAX_LIMIT == 100
    assert feed_service.FEED_THUMBNAILS == 6
    assert feed_service.DEFAULT_CREDIT == "NASA/JPL-Caltech"
