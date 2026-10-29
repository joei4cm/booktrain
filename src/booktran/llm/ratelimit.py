"""限流与退避：并发信号量 + 滑动窗口 RPM + 指数退避重试。

这是相对 AiNiee 的核心增强：AiNiee 对 429/网络错误不做请求级重试，
直接依赖"下一轮"兜底，浪费轮次与时间。
"""
from __future__ import annotations

import asyncio
import random
import time
from collections import deque

import httpx

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


class RateLimiter:
    def __init__(self, rpm: int = 0, concurrency: int = 4) -> None:
        self.rpm = rpm
        self._sem = asyncio.Semaphore(max(1, concurrency))
        self._window: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        await self._sem.acquire()
        if self.rpm > 0:
            while True:
                async with self._lock:
                    now = time.monotonic()
                    while self._window and now - self._window[0] > 60:
                        self._window.popleft()
                    if len(self._window) < self.rpm:
                        self._window.append(now)
                        return
                    wait = 60 - (now - self._window[0]) + 0.05
                await asyncio.sleep(wait)

    def release(self) -> None:
        self._sem.release()


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError) or isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    if isinstance(exc, LLMError):
        return exc.status_code in RETRYABLE_STATUS
    return False


def backoff_delay(attempt: int) -> float:
    base = min(30.0, 1.8 ** attempt)
    return base * (0.5 + random.random())  # 全抖动


class LLMError(Exception):
    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


async def request_with_retry(fn, *, max_retries: int, on_retry=None):
    """fn: async callable -> response。抛 LLMError/httpx 异常时按退避重试。"""
    attempt = 0
    while True:
        try:
            return await fn()
        except Exception as e:  # noqa: BLE001
            if attempt >= max_retries or not is_retryable(e):
                raise
            if on_retry:
                on_retry(attempt, e)
            await asyncio.sleep(backoff_delay(attempt))
            attempt += 1
