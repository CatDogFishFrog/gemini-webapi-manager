"""Сумісність: ManagedClient переїхав у gemini_vault.sessions."""

from gemini_vault.sessions import *  # noqa: F401,F403
from gemini_vault.sessions import (  # noqa: F401
    ManagedChat,
    ManagedClient,
    build_gemini_client,
    cookies_to_dict,
    status_name,
)
