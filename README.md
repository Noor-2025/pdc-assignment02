# Noor Fatima | BSCS23166

## PDC-Sp24-BSCS23166-Fatima

**Course:** Parallel and Distributed Computing (PDC)
**Assignment:** Building Resilient Distributed Systems — Part 3

---

## What This Solves

**Problem:** The StudySync backend calls an external LLM API synchronously with no
timeout and no fallback. When the LLM goes down, every server thread blocks for up
to 60 seconds, making the entire application unresponsive for all users.

**Fix:** A **Circuit Breaker** wraps every LLM call. After three consecutive failures
the breaker opens and all subsequent calls return an instant fallback message —
no network I/O, no blocking, no user-visible hang. The breaker probes the LLM
periodically and closes again once it recovers.

---

## Project Structure

```
.
├── main.py                 # FastAPI application + circuit-breaker-protected /ask route
├── circuit_breaker.py      # CircuitBreaker class (CLOSED / OPEN / HALF_OPEN)
├── test_circuit_breaker.py # Four-scenario test suite (standalone, no server needed)
└── README.md
```

---

## Setup

**Python 3.10 or later required.**

```bash
# 1. Clone
git clone https://github.com/<your-username>/PDC-Sp24-BSCS23166-Fatima.git
cd PDC-Sp24-BSCS23166-Fatima

# 2. Virtual environment
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. Dependencies
pip install fastapi uvicorn httpx pydantic
```

---

## Running the Server

```bash
uvicorn main:app --reload --port 8000
```

Interactive API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## Running the Tests

The test script runs **without** needing the server for Scenarios A–C.

```bash
python test_circuit_breaker.py
```

### What each scenario proves

| Scenario | What it shows |
|----------|--------------|
| **A — Baseline** | Without a breaker, 8 requests × 0.8 s each = ~6.4 s of total blocking. Server is frozen. |
| **B — With breaker** | The first 3 requests pay the latency cost; the remaining 5 return in < 1 ms via fallback. |
| **C — Recovery** | Breaker transitions OPEN → HALF_OPEN → CLOSED once the cooldown elapses and a healthy probe succeeds. |
| **D — Header check** | Confirms `X-Student-ID: BSCS23166` is present on every endpoint (requires live server). |

---

## Verifying the Custom Header

```bash
curl -s -o /dev/null -D - http://localhost:8000/ | grep -i x-student-id
# Expected:  X-Student-ID: BSCS23166
```

Or via HTTPie:

```bash
http GET localhost:8000/status
# Look for  X-Student-ID: BSCS23166  in the response headers
```

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Ping / sanity check |
| GET | `/status` | Service health + breaker snapshot |
| POST | `/ask` | Submit a question (CB-protected) |
| GET | `/breaker` | Inspect breaker state |
| POST | `/breaker/reset` | Force breaker back to CLOSED |

---

## Circuit Breaker States

```
            3 failures
  CLOSED ──────────────► OPEN
    ▲                      │
    │   probe succeeds      │ cooldown (15 s)
    │                      ▼
    └─────────────── HALF_OPEN
         probe fails → OPEN again
```

- **CLOSED:** Normal operation. Hard 5 s timeout on every LLM call.
- **OPEN:** All calls blocked. Instant fallback returned. No network I/O.
- **HALF_OPEN:** One probe allowed. Close on success; reopen on failure.
