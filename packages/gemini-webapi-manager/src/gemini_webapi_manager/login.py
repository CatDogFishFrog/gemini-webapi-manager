"""Сумісність: вхід переїхав у gemini_vault.login."""

from gemini_vault.login import *  # noqa: F401,F403
from gemini_vault.login.common import PSID, PSIDTS, google_cookie_dict, parse_label  # noqa: F401
from gemini_vault.login.browser import browser_login  # noqa: F401
