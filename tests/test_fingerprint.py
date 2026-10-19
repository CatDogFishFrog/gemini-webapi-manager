"""Tests for browser fingerprint generator and context options."""

from __future__ import annotations

from gemini_vault.login.fingerprint import BrowserFingerprint, generate_fingerprint


def test_generate_fingerprint_defaults():
    fp = generate_fingerprint()
    assert isinstance(fp, BrowserFingerprint)
    assert "Chrome" in fp.user_agent
    assert fp.viewport["width"] > 1000
    assert fp.screen["width"] > 1000
    assert fp.locale == "en-US"
    assert "sec-ch-ua" in fp.extra_headers
    assert fp.extra_headers["sec-ch-ua-mobile"] == "?0"


def test_generate_fingerprint_custom():
    fp = generate_fingerprint(platform="macos", locale="uk-UA", timezone_id="Europe/Kyiv")
    assert "Macintosh" in fp.user_agent
    assert fp.locale == "uk-UA"
    assert fp.timezone_id == "Europe/Kyiv"
    assert fp.extra_headers["sec-ch-ua-platform"] == '"macOS"'


def test_fingerprint_context_options():
    fp = generate_fingerprint()
    opts = fp.context_options()
    assert opts["user_agent"] == fp.user_agent
    assert opts["viewport"] == fp.viewport
    assert opts["screen"] == fp.screen
    assert opts["locale"] == fp.locale
    assert opts["extra_http_headers"] == fp.extra_headers


def test_fingerprint_serialization_roundtrip():
    fp = generate_fingerprint()
    data = fp.to_dict()
    restored = BrowserFingerprint.from_dict(data)
    assert fp == restored
