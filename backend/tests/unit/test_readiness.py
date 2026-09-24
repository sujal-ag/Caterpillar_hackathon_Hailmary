"""Phase 5 gate: readiness score table (HLD §7.4; plan.md Phase 5 item 3), every bracket,
including D3 (5 h sleep alone is GREEN; 5 h + < 12 h in 48 h is YELLOW)."""

import pytest

from edge.engine.rules import load_rules
from edge.readiness.score import score

P = load_rules().readiness
R = "readiness.reason."
OK = dict(
    sleep_last_24h_h=8,
    sleep_last_48h_h=16,
    feel_score=4,
    rt_mean_ms=300,
    rt_lapses=0,
    long_blinks_20s=None,
)


def run(heat="NONE", baseline=300, **over):
    return score({**OK, **over}, baseline, heat, P)


def test_all_clear_is_100_green():
    assert run() == {"score": 100, "rating": "GREEN", "reasons": [], "rt_delta_vs_baseline_pct": 0}


@pytest.mark.parametrize(
    "rt,penalty,reason",
    [
        (329, 0, None),  # +9.7 %: under the first bracket
        (330, 10, "rt_slower_10"),  # +10 %
        (360, 25, "rt_slower_20"),  # +20 %
        (390, 40, "rt_slower_30"),  # +30 %
        (600, 40, "rt_slower_30"),  # highest bracket only
    ],
)
def test_reaction_time_brackets_highest_only(rt, penalty, reason):
    out = run(rt_mean_ms=rt)
    assert out["score"] == 100 - penalty
    assert out["reasons"] == ([R + reason] if reason else [])


def test_lapses_cost_10_each():
    assert run(rt_lapses=3)["score"] == 70
    assert run(rt_lapses=3)["reasons"] == [R + "rt_lapses"]


@pytest.mark.parametrize(
    "s24,s48,expect_score,rating",
    [
        (5, 14, 85, "GREEN"),  # D3: 5 h/24 only trips "< 6 h" (−15)
        (5, 11, 70, "YELLOW"),  # D3: + < 12 h/48 -> −30 (highest only)
        (4.9, 20, 70, "YELLOW"),  # < 5 h/24 -> −30
        (5.9, 20, 85, "GREEN"),  # < 6 h/24 -> −15
        (6, 12, 100, "GREEN"),  # boundaries are strict
    ],
)
def test_sleep_highest_penalty_only(s24, s48, expect_score, rating):
    out = run(sleep_last_24h_h=s24, sleep_last_48h_h=s48)
    assert (out["score"], out["rating"]) == (expect_score, rating)


def test_feel_heat_blinks():
    assert run(feel_score=2)["score"] == 85 and run(feel_score=3)["score"] == 100
    assert run(heat="CAUTION")["score"] == 100
    assert run(heat="EXTREME_CAUTION")["score"] == 95
    assert run(heat="DANGER")["score"] == 90
    assert run(long_blinks_20s=2)["score"] == 85 and run(long_blinks_20s=1)["score"] == 100
    assert run(long_blinks_20s=None)["score"] == 100  # camera not used: no penalty


def test_rating_bands_and_floor():
    assert run(rt_lapses=2, feel_score=1)["score"] == 65  # −20 −15
    assert run(rt_lapses=2, feel_score=1)["rating"] == "YELLOW"
    assert run(rt_lapses=2, feel_score=1, sleep_last_24h_h=4)["rating"] == "RED"  # 35
    assert run(rt_lapses=2, sleep_last_24h_h=4)["score"] == 50  # 50 = YELLOW, not RED
    assert run(rt_lapses=2, sleep_last_24h_h=4)["rating"] == "YELLOW"
    assert run(rt_mean_ms=900, rt_lapses=10, sleep_last_24h_h=0, feel_score=1)["score"] == 0


def test_personal_baseline_changes_the_bracket():
    assert run(rt_mean_ms=330, baseline=250)["reasons"] == [R + "rt_slower_30"]  # +32 %
