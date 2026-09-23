from fastapi import FastAPI

from common.timeutil import now_utc, to_site_iso

app = FastAPI(title="Operator Companion cloud-api", version="0.0.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "cloud-api", "ts": to_site_iso(now_utc())}
