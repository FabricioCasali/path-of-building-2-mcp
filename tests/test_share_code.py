"""Round-trip and error tests for the PoB2 share-code codec."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from pob_mcp.share_code import (  # noqa: E402
    ShareCodeError,
    decode_share_code,
    encode_share_code,
)

SAMPLE_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<PathOfBuilding2>\n'
    '  <Build level="90" className="Ranger" ascendClassName="Deadeye"'
    ' mainSocketGroup="1"/>\n'
    '  <Skills/>\n'
    '  <Tree/>\n'
    '</PathOfBuilding2>'
)


def test_round_trip_preserves_xml():
    code = encode_share_code(SAMPLE_XML)
    assert decode_share_code(code) == SAMPLE_XML


def test_code_is_url_safe():
    code = encode_share_code(SAMPLE_XML)
    assert "+" not in code and "/" not in code


def test_decode_tolerates_stripped_padding():
    code = encode_share_code(SAMPLE_XML).rstrip("=")
    assert decode_share_code(code) == SAMPLE_XML


def test_decode_accepts_urlsafe_and_standard_alphabet():
    # A payload whose base64 contains chars that map across alphabets should
    # decode identically whether given url-safe or standard.
    code = encode_share_code(SAMPLE_XML)
    standard = code.replace("-", "+").replace("_", "/")
    assert decode_share_code(standard) == decode_share_code(code)


@pytest.mark.parametrize("bad", ["", "   ", "!!!not base64!!!"])
def test_bad_input_raises(bad):
    with pytest.raises(ShareCodeError):
        decode_share_code(bad)


def test_valid_base64_but_not_compressed_raises():
    import base64

    plain = base64.b64encode(b"just some bytes, not zlib").decode()
    with pytest.raises(ShareCodeError):
        decode_share_code(plain)
