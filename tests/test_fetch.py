"""Unit tests for build-URL resolution (no network — fetch is monkeypatched)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from pob_mcp import fetch  # noqa: E402
from pob_mcp.share_code import encode_share_code  # noqa: E402


def test_looks_like_url():
    assert fetch.looks_like_url("https://pobb.in/abc")
    assert fetch.looks_like_url("http://pastebin.com/raw/x")
    assert not fetch.looks_like_url("eNrtPWtznDi2n6d")  # a share code
    assert not fetch.looks_like_url("<?xml version='1.0'?>")
    assert not fetch.looks_like_url("has spaces so not a url")


def test_looks_like_xml():
    assert fetch.looks_like_xml("<?xml version='1.0'?>\n<PathOfBuilding2>")
    assert fetch.looks_like_xml("  <PathOfBuilding2>")
    assert not fetch.looks_like_xml("eNrtPWtznDi2n6d")


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://pobb.in/abc123", "https://pobb.in/abc123/raw"),
        ("https://pobb.in/abc123/", "https://pobb.in/abc123/raw"),
        ("https://pobb.in/abc123/raw", "https://pobb.in/abc123/raw"),
        ("https://pastebin.com/xyz", "https://pastebin.com/raw/xyz"),
        ("https://pastebin.com/raw/xyz", "https://pastebin.com/raw/xyz"),
        ("https://example.com/whatever", "https://example.com/whatever"),
    ],
)
def test_normalize_to_raw(url, expected):
    assert fetch._normalize_to_raw(url) == expected


def test_resolve_xml_from_url(monkeypatch):
    """A URL should be fetched, then decoded through the normal share-code path."""
    from pob_mcp import server

    xml = "<?xml version='1.0'?>\n<PathOfBuilding2></PathOfBuilding2>"
    code = encode_share_code(xml)
    monkeypatch.setattr(server, "fetch_build_source", lambda url: code)

    resolved = server._resolve_xml("https://pobb.in/whatever", None)
    assert resolved == xml


def test_resolve_xml_url_returning_raw_xml(monkeypatch):
    """Some raw endpoints serve XML directly; that must pass through untouched."""
    from pob_mcp import server

    xml = "<?xml version='1.0'?>\n<PathOfBuilding2></PathOfBuilding2>"
    monkeypatch.setattr(server, "fetch_build_source", lambda url: xml)

    assert server._resolve_xml("https://pobb.in/whatever", None) == xml
