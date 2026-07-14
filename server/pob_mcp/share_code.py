"""Decode/encode Path of Building 2 share codes.

A PoB2 share code is the build XML run through zlib-deflate, then base64, then
made URL-safe by swapping ``+``->``-`` and ``/``->``_``. This mirrors exactly
what the fork does in ``src/Classes/ImportTab.lua``:

    encode: common.base64.encode(Deflate(xml)):gsub("+","-"):gsub("/","_")
    decode: Inflate(common.base64.decode(code:gsub("-","+"):gsub("_","/")))

``Deflate``/``Inflate`` are stubbed to return "" inside the headless wrapper,
so the MCP server does the (de)compression in Python and hands raw XML to
``loadBuildFromXML``. Only stdlib is used (base64 + zlib).
"""

from __future__ import annotations

import base64
import zlib

__all__ = ["decode_share_code", "encode_share_code", "ShareCodeError"]


class ShareCodeError(ValueError):
    """Raised when a share code can't be decoded into build XML."""


def _b64_translate_from_urlsafe(code: str) -> str:
    return code.strip().replace("-", "+").replace("_", "/")


def _b64_translate_to_urlsafe(b64: str) -> str:
    return b64.replace("+", "-").replace("/", "_")


def decode_share_code(code: str) -> str:
    """Turn a PoB2 share code into the raw build XML string.

    Accepts codes with or without ``=`` padding. Raises ``ShareCodeError`` on
    malformed base64 or a payload that isn't zlib-compressed XML.
    """
    if not code or not code.strip():
        raise ShareCodeError("empty share code")

    standard = _b64_translate_from_urlsafe(code)
    # PoB keeps standard base64 '=' padding, but be defensive if it was trimmed.
    padding = (-len(standard)) % 4
    standard += "=" * padding

    try:
        raw = base64.b64decode(standard, validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise ShareCodeError(f"invalid base64 in share code: {exc}") from exc

    try:
        # PoB's Deflate emits a zlib stream (header + adler32), so plain
        # zlib.decompress handles it. Fall back to raw DEFLATE just in case.
        xml_bytes = zlib.decompress(raw)
    except zlib.error:
        try:
            xml_bytes = zlib.decompress(raw, -zlib.MAX_WBITS)
        except zlib.error as exc:
            raise ShareCodeError(f"payload is not zlib-compressed: {exc}") from exc

    try:
        return xml_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ShareCodeError(f"decompressed payload is not UTF-8 XML: {exc}") from exc


def encode_share_code(xml: str) -> str:
    """Turn raw build XML into a PoB2 share code (inverse of decode).

    Useful for round-trip tests and for handing a share code back to the user.
    """
    compressed = zlib.compress(xml.encode("utf-8"), level=9)
    b64 = base64.b64encode(compressed).decode("ascii")
    return _b64_translate_to_urlsafe(b64)
