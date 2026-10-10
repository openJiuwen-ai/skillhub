# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""k8s executor 端到端冒烟。"""
from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

import json
import os

import httpx

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8900")
RUN_LLM = os.environ.get("SMOKE_LLM", "0") == "1"
API = f"{BASE}/api/v1/playground"


async def _collect_until_done(client: httpx.AsyncClient, sid: str, sink: list[dict]) -> None:
    async with client.stream("GET", f"{API}/sessions/{sid}/stream") as resp:
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[len("data:"):].strip())
            sink.append(ev)
            if ev.get("type") in ("done", "session_ended"):
                return


async def _one_turn(client: httpx.AsyncClient, sid: str, content: str, timeout: float) -> list[dict]:
    events: list[dict] = []
    reader = asyncio.create_task(_collect_until_done(client, sid, events))
    await asyncio.sleep(0.3)
    await client.post(f"{API}/sessions/{sid}/messages", json={"content": content})
    await asyncio.wait_for(reader, timeout=timeout)
    return events


async def main() -> int:
    failures: list[str] = []
    async with httpx.AsyncClient(timeout=120) as client:
        health = (await client.get(f"{BASE}/health")).json()
        logger.info("health: %s", health)
        if health.get("executor") != "k8s":
            logger.info("WARN: control plane executor is not 'k8s'")

        created = (await client.post(
            f"{API}/sessions",
            json={"skill_id": "k8s-smoke", "version": "1.0.0",
                  "system_prompt": "You are a test skill running in an isolated pod."},
        )).json()
        sid = created.get("session_id")
        logger.info("created: %s", created)
        if created.get("status") == "error" or not sid:
            logger.info("RESULT: FAIL (session create failed — pod did not come up)")
            return 1

        ev1 = await _one_turn(client, sid, "!echo hello-from-pod", timeout=90)
        logger.info("stage1 events: %s", [e.get("type") for e in ev1])
        tool_results = [e for e in ev1 if e.get("type") == "tool_result"]
        if not any("hello-from-pod" in (e.get("output") or "") for e in tool_results):
            failures.append("stage1: pod sandbox echo not observed")

        if RUN_LLM:
            ev2 = await _one_turn(client, sid, "用一句话说你在哪运行。", timeout=float(os.environ.get("SMOKE_LLM_TIMEOUT", "300")))
            logger.info("stage2 events: %s", [e.get("type") for e in ev2])
            got_text = any(e.get("type") in ("text", "answer") for e in ev2)
            err = [e for e in ev2 if e.get("type") == "error"]
            if not got_text:
                failures.append(f"stage2: no LLM text/answer (errors={err})")

        await client.delete(f"{API}/sessions/{sid}")

    if failures:
        logger.info("RESULT: FAIL")
        for f in failures:
            logger.info("  - %s", f)
        return 1
    logger.info("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
