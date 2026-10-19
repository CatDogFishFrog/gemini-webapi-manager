"""Tests for human-like interaction emulation (mouse movements, delays, keystroke typing)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from gemini_vault.login.human import (
    HumanAgent,
    _bezier_point,
    _ease_in_out,
    human_delay,
)


def test_ease_in_out():
    assert _ease_in_out(0.0) == 0.0
    assert _ease_in_out(1.0) == 1.0
    mid = _ease_in_out(0.5)
    assert 0.49 <= mid <= 0.51


def test_bezier_point():
    p0 = (0.0, 0.0)
    p1 = (10.0, 20.0)
    p2 = (90.0, 80.0)
    p3 = (100.0, 100.0)

    start = _bezier_point(p0, p1, p2, p3, 0.0)
    assert start == (0.0, 0.0)

    end = _bezier_point(p0, p1, p2, p3, 1.0)
    assert end == (100.0, 100.0)

    mid = _bezier_point(p0, p1, p2, p3, 0.5)
    assert 0 < mid[0] < 100 and 0 < mid[1] < 100


def test_human_delay():
    async def run():
        loop_start = asyncio.get_event_loop().time()
        await human_delay(0.01, 0.02)
        elapsed = asyncio.get_event_loop().time() - loop_start
        assert elapsed >= 0.008

    asyncio.run(run())


def test_human_agent_move_mouse():
    async def run():
        page = MagicMock()
        page.mouse = MagicMock()
        page.mouse.move = AsyncMock()

        agent = HumanAgent(page)
        agent.cur_x, agent.cur_y = 100.0, 100.0

        await agent.move_mouse(300.0, 400.0)

        assert agent.cur_x == 300.0
        assert agent.cur_y == 400.0
        assert page.mouse.move.await_count > 5

    asyncio.run(run())


def test_human_agent_click_locator_with_box():
    async def run():
        page = MagicMock()
        page.mouse = MagicMock()
        page.mouse.move = AsyncMock()
        page.mouse.down = AsyncMock()
        page.mouse.up = AsyncMock()

        locator = MagicMock()
        locator.bounding_box = AsyncMock(return_value={"x": 50, "y": 50, "width": 100, "height": 40})
        locator.click = AsyncMock()

        agent = HumanAgent(page)
        agent.cur_x, agent.cur_y = 0.0, 0.0

        await agent.click_locator(locator)

        assert page.mouse.down.await_count == 1
        assert page.mouse.up.await_count == 1
        locator.click.assert_not_called()

    asyncio.run(run())


def test_human_agent_click_locator_fallback():
    async def run():
        page = MagicMock()
        page.mouse = MagicMock()
        locator = MagicMock()
        locator.bounding_box = AsyncMock(return_value=None)
        locator.click = AsyncMock()

        agent = HumanAgent(page)
        await agent.click_locator(locator)

        locator.click.assert_awaited_once()

    asyncio.run(run())


def test_human_agent_type_text():
    async def run():
        page = MagicMock()
        page.mouse = MagicMock()
        page.mouse.move = AsyncMock()
        page.mouse.down = AsyncMock()
        page.mouse.up = AsyncMock()
        page.keyboard = MagicMock()
        page.keyboard.type = AsyncMock()

        locator = MagicMock()
        locator.bounding_box = AsyncMock(return_value={"x": 10, "y": 10, "width": 80, "height": 30})
        locator.fill = AsyncMock()

        agent = HumanAgent(page)
        await agent.type_text(locator, "test@example.com")

        locator.fill.assert_awaited_with("")
        assert page.keyboard.type.await_count == len("test@example.com")

    asyncio.run(run())
