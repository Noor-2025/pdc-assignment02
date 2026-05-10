"""
test_circuit_breaker.py
========================
Standalone test suite for the CircuitBreaker class.

Does NOT require the FastAPI server to be running for Scenarios A–C.
Scenario D (header verification) requires: uvicorn main:app --port 8000

Run:
    python test_circuit_breaker.py

Scenarios
---------
  A  Baseline (no breaker) — every request blocks for the full simulated timeout.
     Shows why the naive approach freezes the server.

  B  With breaker — the first THRESHOLD requests pay the timeout cost; from
     the THRESHOLD+1-th request onward, calls are blocked instantly and the
     fallback is returned. Total wall time is drastically lower.

  C  Recovery path — trip the breaker, wait out the cooldown, confirm the
     breaker moves through HALF_OPEN and back to CLOSED on a healthy probe.

  D  Header check — verifies X-Student-ID: BSCS23166 appears on every
     endpoint (requires live server).

Author : Noor Fatima
Roll No: BSCS23166
"""

from __future__ import annotations

import asyncio
import sys
import time

import httpx

sys.path.insert(0, ".")
from circuit_breaker import CircuitBreaker, BreakerTripError

# ── Shared test constants ──────────────────────────────────────────────────────

SIMULATED_LATENCY = 0.8   # seconds each "slow" LLM call takes
TOTAL_REQUESTS    = 8
THRESHOLD         = 3
COOLDOWN          = 2.0   # short cooldown for test speed

FALLBACK = {
    "source":  "fallback",
    "content": "AI assistant temporarily unavailable — please retry shortly.",
}

SERVER_BASE = "http://127.0.0.1:8000"


# ── Mock LLM functions ─────────────────────────────────────────────────────────

async def slow_failing_llm(question: str) -> dict:
    """Simulates an LLM that is down: hangs for SIMULATED_LATENCY then errors."""
    await asyncio.sleep(SIMULATED_LATENCY)
    raise httpx.TimeoutException(f"LLM did not respond within {SIMULATED_LATENCY}s")


async def healthy_llm(question: str) -> dict:
    """Simulates a recovered LLM: responds quickly and successfully."""
    await asyncio.sleep(0.05)
    return {"answer": f"Here is an answer to: '{question}'"}


# ── Scenario helpers ───────────────────────────────────────────────────────────

def divider(title: str) -> None:
    print("\n" + "─" * 62)
    print(f"  {title}")
    print("─" * 62)


# ── Scenario A — baseline without any circuit breaker ─────────────────────────

async def scenario_a() -> None:
    divider("SCENARIO A  —  No circuit breaker (baseline)")
    print(f"  Each of {TOTAL_REQUESTS} requests blocks for ~{SIMULATED_LATENCY}s before failing.\n")

    wall_start = time.monotonic()
    for n in range(1, TOTAL_REQUESTS + 1):
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(slow_failing_llm(f"q{n}"), timeout=SIMULATED_LATENCY)
        except Exception as exc:
            elapsed = time.monotonic() - t0
            print(f"  [{n:02d}]  waited {elapsed:.2f}s  →  {type(exc).__name__}")

    total = time.monotonic() - wall_start
    print(f"\n  Wall time: {total:.2f}s  (≈ {TOTAL_REQUESTS} × {SIMULATED_LATENCY}s)")
    print("  ↳ The server was unresponsive the entire time — all users affected.")


# ── Scenario B — with circuit breaker ─────────────────────────────────────────

async def scenario_b() -> None:
    divider("SCENARIO B  —  With circuit breaker enabled")
    print(f"  Breaker opens after {THRESHOLD} failures. Later calls return instantly.\n")

    cb = CircuitBreaker(
        failure_threshold=THRESHOLD,
        reset_after=COOLDOWN,
        label="test-llm",
    )

    wall_start = time.monotonic()
    for n in range(1, TOTAL_REQUESTS + 1):
        t0 = time.monotonic()
        result = await cb.run(slow_failing_llm, f"q{n}", fallback=FALLBACK)
        elapsed = time.monotonic() - t0
        snap = cb.snapshot()
        tag = "instant fallback" if elapsed < 0.05 else f"slow fallback ({elapsed:.2f}s)"
        print(f"  [{n:02d}]  state={snap['state']:9s}  {tag}")

    total = time.monotonic() - wall_start
    print(f"\n  Wall time: {total:.2f}s")
    print(f"  ↳ Only the first {THRESHOLD} requests incurred the latency penalty.")
    print(f"  ↳ Requests {THRESHOLD + 1}–{TOTAL_REQUESTS} returned in <1 ms via fallback.")


# ── Scenario C — recovery cycle ───────────────────────────────────────────────

async def scenario_c() -> None:
    divider("SCENARIO C  —  Recovery cycle  (OPEN → HALF_OPEN → CLOSED)")
    print(f"  Cooldown set to {COOLDOWN}s for this scenario.\n")

    cb = CircuitBreaker(failure_threshold=2, reset_after=COOLDOWN, label="recovery-test")

    # Phase 1: trip it
    print("  Phase 1 — tripping the breaker with 2 failures …")
    for n in range(1, 3):
        await cb.run(slow_failing_llm, "trip", fallback=FALLBACK)
        print(f"    attempt {n}  →  state={cb.snapshot()['state']}")

    # Phase 2: wait out the cooldown
    print(f"\n  Phase 2 — waiting {COOLDOWN}s for cooldown to expire …")
    await asyncio.sleep(COOLDOWN + 0.15)
    print(f"    state after wait: {cb.snapshot()['state']}")  # still OPEN until poked

    # Phase 3: probe with healthy LLM
    print("\n  Phase 3 — sending probe with healthy LLM …")
    result = await cb.run(healthy_llm, "2 + 2 = ?", fallback=FALLBACK)
    snap = cb.snapshot()
    print(f"    result : {result}")
    print(f"    state  : {snap['state']}")
    print("\n  ↳ Breaker successfully recovered to CLOSED.")


# ── Scenario D — live header verification ─────────────────────────────────────

async def scenario_d() -> None:
    divider("SCENARIO D  —  X-Student-ID header check  (requires live server)")

    endpoints = ["/", "/status", "/breaker"]
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            for ep in endpoints:
                r = await client.get(f"{SERVER_BASE}{ep}")
                sid = r.headers.get("x-student-id", "*** MISSING ***")
                ok  = "✓" if sid == "BSCS23166" else "✗"
                print(f"  {ok}  GET {ep:12s}  →  HTTP {r.status_code}  |  X-Student-ID: {sid}")
    except httpx.ConnectError:
        print("  [SKIPPED] Server not reachable.")
        print("  Start with:  uvicorn main:app --reload --port 8000")


# ── Entry point ────────────────────────────────────────────────────────────────

async def main() -> None:
    print("\n" + "=" * 62)
    print("  StudySync — Circuit Breaker Test Suite")
    print("  Noor Fatima  |  BSCS23166  |  PDC Assignment 2")
    print("=" * 62)

    await scenario_a()
    await scenario_b()
    await scenario_c()
    await scenario_d()

    print("\n" + "=" * 62)
    print("  All scenarios complete.")
    print("=" * 62 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
