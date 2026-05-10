"""
main.py
=======
StudySync FastAPI backend — PDC Assignment 2, Part 3.

Demonstrates the Circuit Breaker pattern to fix the LLM fault-tolerance bug:
  Before: A slow/down LLM blocks every server thread for up to 60 seconds.
  After : The breaker short-circuits failing calls and returns an instant
          fallback, keeping the rest of the application fully responsive.

Every HTTP response carries the mandatory header:
    X-Student-ID: BSCS23166

Author : Noor Fatima
Roll No: BSCS23166
Course : Parallel and Distributed Computing (PDC)
"""

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from circuit_breaker import BreakerTripError, CircuitBreaker

# ── Configuration ──────────────────────────────────────────────────────────────

STUDENT_ID      = "BSCS23166"
LLM_ENDPOINT    = "http://127.0.0.1:9999/generate"
LLM_TIMEOUT_SEC = 5.0
BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN  = 15.0

FALLBACK_PAYLOAD = {
    "source":  "fallback",
    "content": (
        "The AI assistant is currently unavailable. "
        "Your request has been noted — please retry in a moment."
    ),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
log = logging.getLogger("studysync")

# ── Circuit breaker instance (application-scoped singleton) ───────────────────

breaker = CircuitBreaker(
    failure_threshold=BREAKER_THRESHOLD,
    reset_after=BREAKER_COOLDOWN,
    label="llm-service",
)


# ── Application lifespan ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app: FastAPI):
    log.info("StudySync starting — student %s", STUDENT_ID)
    yield
    log.info("StudySync shutting down")


# ── App setup ──────────────────────────────────────────────────────────────────

app = FastAPI(
    title="StudySync — Resilient Backend",
    description="PDC Assignment 2: Circuit Breaker demonstration",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Mandatory custom header middleware ─────────────────────────────────────────

class StudentHeaderMiddleware(BaseHTTPMiddleware):
    """Appends X-Student-ID: BSCS23166 to every outgoing response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Student-ID"] = STUDENT_ID
        return response


app.add_middleware(StudentHeaderMiddleware)


# ── Request / response schemas ─────────────────────────────────────────────────

class PromptIn(BaseModel):
    question: str = Field(..., min_length=1, description="The question to send to the LLM")


# ── Internal LLM caller ────────────────────────────────────────────────────────

async def _invoke_llm(question: str) -> dict:
    """
    Forward a question to the external LLM service.
    Uses a hard timeout so no request can ever block for 60 seconds.
    """
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SEC) as session:
        resp = await session.post(LLM_ENDPOINT, json={"question": question})
        resp.raise_for_status()
        return resp.json()


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/", tags=["health"], summary="Ping")
async def ping():
    return {"service": "StudySync", "student_id": STUDENT_ID, "status": "running"}


@app.get("/status", tags=["health"], summary="Full health check")
async def service_status():
    return {
        "student_id": STUDENT_ID,
        "llm_circuit": breaker.snapshot(),
    }


@app.post("/ask", tags=["llm"], summary="Ask the LLM (circuit-breaker protected)")
async def ask(payload: PromptIn):
    """
    Route the question through the circuit breaker.

    - While CLOSED: calls the real LLM (max 5 s timeout).
    - While OPEN: returns the fallback immediately, no network call made.
    - HALF_OPEN: sends a single probe; closes on success, reopens on failure.
    """
    answer = await breaker.run(
        _invoke_llm,
        payload.question,
        fallback=FALLBACK_PAYLOAD,
    )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "answer": answer,
            "breaker": breaker.snapshot(),
        },
    )


@app.get("/breaker", tags=["circuit-breaker"], summary="Inspect breaker state")
async def breaker_state():
    return breaker.snapshot()


@app.post("/breaker/reset", tags=["circuit-breaker"], summary="Force breaker to CLOSED")
async def breaker_reset():
    breaker.force_close()
    return {"message": "Circuit breaker reset to CLOSED.", "breaker": breaker.snapshot()}
