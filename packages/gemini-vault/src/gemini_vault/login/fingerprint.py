"""Browser fingerprinting: realistic fingerprint generator and session context configuration.

Provides plausible browser profiles (User-Agent, Viewport, Screen, Client Hints, Locales)
to minimize bot detection. In the future, each account will have its persistent profile
stored in the vault alongside session cookies.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from typing import Any

# Актуальні стабільні версії Chrome та системні платформи
_CHROME_BUILDS = [
    ("133.0.6943.98", "133"),
    ("132.0.6834.160", "132"),
    ("131.0.6778.205", "131"),
]

_PLATFORM_CONFIGS = {
    "windows": {
        "os_token": "Windows NT 10.0; Win64; x64",
        "sec_ch_ua_platform": '"Windows"',
    },
    "macos": {
        "os_token": "Macintosh; Intel Mac OS X 10_15_7",
        "sec_ch_ua_platform": '"macOS"',
    },
    "linux": {
        "os_token": "X11; Linux x86_64",
        "sec_ch_ua_platform": '"Linux"',
    },
}

_VIEWPORTS = [
    {"viewport": {"width": 1920, "height": 1080}, "screen": {"width": 1920, "height": 1080}},
    {"viewport": {"width": 1536, "height": 864}, "screen": {"width": 1536, "height": 864}},
    {"viewport": {"width": 1440, "height": 900}, "screen": {"width": 1440, "height": 900}},
    {"viewport": {"width": 1366, "height": 768}, "screen": {"width": 1366, "height": 768}},
]


@dataclass
class BrowserFingerprint:
    """Правдоподібний профіль браузера для ініціалізації сесії."""

    user_agent: str
    viewport: dict[str, int]
    screen: dict[str, int]
    locale: str = "en-US"
    timezone_id: str = "America/New_York"
    color_scheme: str = "light"
    device_scale_factor: float = 1.0
    extra_headers: dict[str, str] = field(default_factory=dict)

    def context_options(self) -> dict[str, Any]:
        """Аргументи для browser.new_context(**options)."""
        options: dict[str, Any] = {
            "user_agent": self.user_agent,
            "viewport": self.viewport,
            "screen": self.screen,
            "locale": self.locale,
            "timezone_id": self.timezone_id,
            "color_scheme": self.color_scheme,
            "device_scale_factor": self.device_scale_factor,
        }
        if self.extra_headers:
            options["extra_http_headers"] = dict(self.extra_headers)
        return options

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BrowserFingerprint:
        return cls(**data)


def generate_fingerprint(
    *,
    platform: str = "windows",
    locale: str = "en-US",
    timezone_id: str = "America/New_York",
) -> BrowserFingerprint:
    """Згенерувати правдоподібний відбиток Chrome для нового контексту браузера."""
    plat_key = platform.lower()
    if plat_key not in _PLATFORM_CONFIGS:
        plat_key = "windows"
    plat_info = _PLATFORM_CONFIGS[plat_key]

    version_full, version_major = random.choice(_CHROME_BUILDS)
    dims = random.choice(_VIEWPORTS)

    user_agent = (
        f"Mozilla/5.0 ({plat_info['os_token']}) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{version_full} Safari/537.36"
    )

    sec_ch_ua = (
        f'"Not(A:Brand";v="99", "Google Chrome";v="{version_major}", "Chromium";v="{version_major}"'
    )

    headers = {
        "Accept-Language": f"{locale},en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
        "sec-ch-ua": sec_ch_ua,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": plat_info["sec_ch_ua_platform"],
    }

    return BrowserFingerprint(
        user_agent=user_agent,
        viewport=dims["viewport"],
        screen=dims["screen"],
        locale=locale,
        timezone_id=timezone_id,
        color_scheme="light",
        device_scale_factor=1.0,
        extra_headers=headers,
    )
