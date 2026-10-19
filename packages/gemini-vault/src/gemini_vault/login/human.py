"""Human-like interactions for browser automation: realistic mouse movement, typing, and delays.

Emulates human behavior (Bézier curve mouse trajectories, non-uniform typing rhythms,
natural pauses) to avoid heuristics used by anti-bot systems like Google Sign-In.
"""

from __future__ import annotations

import asyncio
import math
import random
from typing import Any


async def human_delay(min_seconds: float = 0.25, max_seconds: float = 0.7) -> None:
    """Асинхронна пауза з випадковою тривалістю."""
    await asyncio.sleep(random.uniform(min_seconds, max_seconds))


def _bezier_point(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    """Кубічна крива Безьє для гладкої траєкторії курсора."""
    u = 1.0 - t
    tt = t * t
    uu = u * u
    uuu = uu * u
    ttt = tt * t

    x = uuu * p0[0] + 3 * uu * t * p1[0] + 3 * u * tt * p2[0] + ttt * p3[0]
    y = uuu * p0[1] + 3 * uu * t * p1[1] + 3 * u * tt * p2[1] + ttt * p3[1]
    return (x, y)


def _ease_in_out(t: float) -> float:
    """Функція плавності (easing) для імітації прискорення та уповільнення руки."""
    return t * t * (3.0 - 2.0 * t)


class HumanAgent:
    """Керує людськими рухами миші та введенням з клавіатури на сторінці."""

    def __init__(self, page: Any) -> None:
        self.page = page
        self.cur_x = float(random.randint(150, 400))
        self.cur_y = float(random.randint(150, 300))

    async def move_mouse(self, target_x: float, target_y: float) -> None:
        """Перемістити курсор по плавній дузі Безьє до цільових координат."""
        dx = target_x - self.cur_x
        dy = target_y - self.cur_y
        dist = math.hypot(dx, dy)

        if dist < 5:
            await self.page.mouse.move(target_x, target_y)
            self.cur_x, self.cur_y = target_x, target_y
            return

        # Кількість проміжних кроків залежно від відстані
        steps = max(12, min(35, int(dist / 20)))

        # Контрольні точки з випадковим відхиленням для природної кривини
        deviation = min(80.0, dist * 0.25)
        p0 = (self.cur_x, self.cur_y)
        p3 = (target_x, target_y)

        # Перпендикулярне зміщення для контрольних точок
        nx = -dy / dist * random.uniform(-deviation, deviation)
        ny = dx / dist * random.uniform(-deviation, deviation)

        p1 = (self.cur_x + dx * 0.3 + nx, self.cur_y + dy * 0.3 + ny)
        p2 = (self.cur_x + dx * 0.7 + nx * 0.5, self.cur_y + dy * 0.7 + ny * 0.5)

        for step in range(1, steps + 1):
            raw_t = step / steps
            t = _ease_in_out(raw_t)
            bx, by = _bezier_point(p0, p1, p2, p3, t)

            # Легке мікро-тремтіння (мимовільні коливання руки)
            jitter_x = random.uniform(-1.0, 1.0) if step < steps else 0.0
            jitter_y = random.uniform(-1.0, 1.0) if step < steps else 0.0

            await self.page.mouse.move(bx + jitter_x, by + jitter_y)
            await asyncio.sleep(random.uniform(0.006, 0.016))

        self.cur_x, self.cur_y = target_x, target_y

    async def click_locator(self, locator: Any, *, timeout: float = 5000) -> None:
        """Знайти координати елемента, перемістити мишу і клікнути природно."""
        try:
            box = await locator.bounding_box(timeout=timeout)
        except Exception:  # noqa: BLE001
            box = None

        if box and box["width"] > 2 and box["height"] > 2:
            # Клік не в ідеальний математичний центр, а зі зміщенням
            target_x = box["x"] + box["width"] * random.uniform(0.25, 0.75)
            target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)

            await self.move_mouse(target_x, target_y)
            await human_delay(0.06, 0.16)
            await self.page.mouse.down()
            await asyncio.sleep(random.uniform(0.05, 0.12))
            await self.page.mouse.up()
            await human_delay(0.2, 0.4)
        else:
            # Якщо bounding box недоступний, використовуємо стандартний клік
            await locator.click(timeout=timeout)
            await human_delay(0.2, 0.4)

    async def type_text(
        self,
        locator: Any,
        text: str,
        *,
        is_secret: bool = False,
    ) -> None:
        """Клікнути в поле вводу та надрукувати текст символ за символом із затримками."""
        await self.click_locator(locator)
        await human_delay(0.15, 0.35)

        # Очистити поле
        await locator.fill("")
        await human_delay(0.1, 0.2)

        pause_chars = {"@", ".", "-", "_", " ", "/"}
        chars_typed = 0

        for char in text:
            await self.page.keyboard.type(char)
            chars_typed += 1

            # Базова затримка між натисканнями клавіш
            delay = random.uniform(0.05, 0.14)

            # Додаткова пауза на розділових символах
            if char in pause_chars:
                delay += random.uniform(0.12, 0.25)
            # Кожні 5-7 символів — природна мікро-заминка
            elif chars_typed % random.randint(5, 7) == 0:
                delay += random.uniform(0.08, 0.18)

            await asyncio.sleep(delay)

        # Пауза після завершення друку перед наступною дією
        await human_delay(0.3, 0.6)
