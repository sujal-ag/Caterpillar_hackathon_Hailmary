"""ML model status (plan.md §5.6). Phase 6 replaces this with the real `ml_runtime` loader,
hot-swap and fallbacks; until then no model is loaded, so both report UNAVAILABLE and the
rules run without them (R23 stays UNKNOWN; ETA uses the generic fallback once it exists)."""


def status() -> dict:
    return {"eta": "UNAVAILABLE", "anomaly": "UNAVAILABLE"}
