"""Shift-start readiness score (HLD §7.4; plan.md Phase 5 item 3). Pure: P1 may replace the body
behind the same signature. Advisory only; nothing reads the rating to block anything (I10).

Start 100. Reaction time: only the highest bracket of % slower than baseline applies; −10 per
lapse. Sleep: only the highest penalty applies. Feeling ≤ 2, heat, camera long blinks. Floor 0.
Every number comes from `rules.yaml` `readiness:`. `reasons` are i18n keys
(`contracts/i18n/en.json`).
"""

_R = "readiness.reason."


def score(inputs: dict, baseline_rt_ms: float, heat_level: str, params: dict) -> dict:
    p, reasons = params, []
    total = p["start"]

    delta_pct = (inputs["rt_mean_ms"] - baseline_rt_ms) / baseline_rt_ms * 100
    for pct, penalty in p["rt_brackets"]:  # highest first
        if delta_pct >= pct:
            total -= penalty
            reasons.append(f"{_R}rt_slower_{pct}")
            break
    lapses = inputs["rt_lapses"]
    if lapses:
        total -= p["lapse_penalty"] * lapses
        reasons.append(f"{_R}rt_lapses")

    s24, s48 = inputs["sleep_last_24h_h"], inputs["sleep_last_48h_h"]
    red, short = p["sleep_red"], p["sleep_short"]
    if s24 < red["sleep_24h_lt"] or s48 < red["sleep_48h_lt"]:
        total -= red["penalty"]
        reasons.append(f"{_R}sleep_low")
    elif s24 < short["sleep_24h_lt"]:
        total -= short["penalty"]
        reasons.append(f"{_R}sleep_short")

    if inputs["feel_score"] <= p["feel_le"]:
        total -= p["feel_penalty"]
        reasons.append(f"{_R}feel_low")

    heat = p["heat_penalty"].get(heat_level)
    if heat:
        total -= heat
        reasons.append(f"{_R}heat_{heat_level.lower()}")

    blinks = inputs.get("long_blinks_20s")
    if blinks is not None and blinks >= p["long_blinks_ge"]:
        total -= p["long_blinks_penalty"]
        reasons.append(f"{_R}long_blinks")

    total = max(0, total)
    rating = "GREEN" if total >= p["green_ge"] else "YELLOW" if total >= p["yellow_ge"] else "RED"
    return {
        "score": total,
        "rating": rating,
        "reasons": reasons,
        "rt_delta_vs_baseline_pct": round(delta_pct, 1),
    }
