import os
import secrets
from threading import Lock
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title="Demo Partner API", version="1.0.0")
job_poll_counts: dict[str, int] = {}
secure_job_poll_counts: dict[str, int] = {}
job_poll_lock = Lock()
demo_partner_token = os.getenv("PARTNER_API_DEMO_TOKEN", "flowforge-local-demo-token")


def require_demo_bearer(authorization: str | None) -> None:
    expected = f"Bearer {demo_partner_token}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Valid demo Bearer authentication is required")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/customers/{customer_id}/score")
def customer_score(customer_id: str) -> dict:
    scores = {
        "CUST-1001": (86, "gold"),
        "CUST-1002": (42, "standard"),
        "CUST-1003": (71, "silver"),
    }
    score, segment = scores.get(customer_id.upper(), (55, "new"))
    return {
        "customerId": customer_id.upper(),
        "score": score,
        "segment": segment,
        "source": "demo-partner-api",
    }


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    normalized_id = job_id.upper()
    with job_poll_lock:
        poll_count = job_poll_counts.get(normalized_id, 0) + 1
        completed = poll_count >= 3
        if completed:
            job_poll_counts.pop(normalized_id, None)
        else:
            job_poll_counts[normalized_id] = poll_count
    return {
        "jobId": normalized_id,
        "status": "COMPLETED" if completed else "PROCESSING",
        "pollCount": poll_count,
        "result": {"message": "Demo job finished"} if completed else None,
        "source": "demo-partner-api",
    }


@app.get("/secure/jobs/{job_id}/submit")
def submit_secure_job(
    job_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    require_demo_bearer(authorization)
    normalized_id = job_id.upper()
    with job_poll_lock:
        secure_job_poll_counts[normalized_id] = 0
    return {
        "jobId": normalized_id,
        "status": "ACCEPTED",
        "authenticated": True,
        "source": "demo-partner-api",
    }


@app.get("/secure/jobs/{job_id}")
def secure_job_status(
    job_id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    require_demo_bearer(authorization)
    normalized_id = job_id.upper()
    with job_poll_lock:
        poll_count = secure_job_poll_counts.get(normalized_id, 0) + 1
        completed = poll_count >= 3
        if completed:
            secure_job_poll_counts.pop(normalized_id, None)
        else:
            secure_job_poll_counts[normalized_id] = poll_count
    return {
        "jobId": normalized_id,
        "status": "COMPLETED" if completed else "PROCESSING",
        "pollCount": poll_count,
        "approved": not normalized_id.endswith("DENY") if completed else None,
        "authenticated": True,
        "source": "demo-partner-api",
    }
