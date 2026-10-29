"""models.dev 供应商模板缓存代理。

从 https://models.dev/api.json 拉取供应商与模型元数据，
缓存 24 小时，供前端"从模板选择"用。
只取文本/视觉模型（过滤掉 TTS/STT/embedding 等）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

MODELS_DEV_URL = "https://models.dev/api.json"
CACHE_TTL = 86400  # 24h
CACHE_FILE = Path.home() / ".cache" / "booktran" / "models_dev.json"


def _should_keep_model(m: dict) -> bool:
    """只保留文本/视觉输入、文本输出的模型（翻译场景）。"""
    modalities = m.get("modalities", {})
    inputs = set(modalities.get("input", []))
    outputs = set(modalities.get("output", []))
    if "text" not in outputs:
        return False
    # 排除纯 audio/video 输入
    if inputs and not inputs & {"text", "image", "pdf"}:
        return False
    # 排除 TTS/STT 类型
    if m.get("type") in ("speech", "transcription"):
        return False
    return True


def _normalize(raw: dict[str, Any]) -> list[dict]:
    """把 models.dev 原始数据转成前端友好的结构。"""
    providers: list[dict] = []
    for pid, pdata in raw.items():
        if not isinstance(pdata, dict):
            continue
        models_raw = pdata.get("models", {})
        if not isinstance(models_raw, dict):
            continue
        models = []
        for mid, m in models_raw.items():
            if not isinstance(m, dict) or not _should_keep_model(m):
                continue
            modalities = m.get("modalities", {})
            inputs = set(modalities.get("input", []))
            cost = m.get("cost", {}) or {}
            models.append({
                "id": mid,
                "name": m.get("name", mid),
                "description": m.get("description", ""),
                "context": (m.get("limit") or {}).get("context", 0),
                "output": (m.get("limit") or {}).get("output", 0),
                "price_in": cost.get("input"),
                "price_out": cost.get("output"),
                "vision": bool(inputs & {"image", "pdf"}),
            })
        if not models:
            continue
        providers.append({
            "id": pid,
            "name": pdata.get("name", pid),
            "api": pdata.get("api", ""),
            "env": pdata.get("env", []),
            "doc": pdata.get("doc", ""),
            "models": sorted(models, key=lambda m: m["name"]),
        })
    return sorted(providers, key=lambda p: p["name"].lower())


async def fetch_providers(force: bool = False) -> list[dict]:
    """拉取并缓存供应商模板。"""
    if not force and CACHE_FILE.exists():
        age = time.time() - CACHE_FILE.stat().st_mtime
        if age < CACHE_TTL:
            try:
                return json.loads(CACHE_FILE.read_text("utf-8"))
            except (OSError, ValueError):
                pass
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(MODELS_DEV_URL)
        resp.raise_for_status()
        raw = resp.json()
    providers = _normalize(raw)
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(providers, ensure_ascii=False), encoding="utf-8")
    return providers
