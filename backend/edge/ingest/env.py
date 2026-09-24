"""Derived environment fields (plan.md Phase 2 item 5; HLD §6.12).

All three formulas take/return Celsius; heat index runs the calculation in Fahrenheit
internally (it's a Fahrenheit regression fit) and converts back, per plan.md's explicit
"converted back to °C" instruction.
"""

import math

# Source priority for `current_env[site]` (HLD §4.2 item 5): a fresher forecast must never
# clobber a live sensor/manual reading.
_SOURCE_RANK = {"MACHINE_SENSOR": 3, "MANUAL": 3, "SIM": 2, "API": 1, "FORECAST_CACHE": 0}

_HEAT_LEVEL_BANDS = (  # (upper bound exclusive, level) — HLD/plan.md Phase 2 item 5
    (27, "NONE"),
    (32, "CAUTION"),
    (39, "EXTREME_CAUTION"),
)
_HEAT_LEVEL_ABOVE = "DANGER"


def dew_point_c(temp_c: float, rh_pct: float) -> float:
    """Magnus formula (HLD §4.2 item 5)."""
    gamma = math.log(rh_pct / 100.0) + 17.62 * temp_c / (243.12 + temp_c)
    return 243.12 * gamma / (17.62 - gamma)


def heat_index_c(temp_c: float, rh_pct: float) -> float:
    """NOAA Rothfusz regression, with the NWS simple-formula fallback below ~80°F
    (HLD §4.2 item 5). `rh_pct` is 0-100."""
    t = temp_c * 9 / 5 + 32
    r = rh_pct
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + r * 0.094)
    if (simple + t) / 2 < 80:
        hi_f = simple
    else:
        hi_f = (
            -42.379
            + 2.04901523 * t
            + 10.14333127 * r
            - 0.22475541 * t * r
            - 0.00683783 * t * t
            - 0.05481717 * r * r
            + 0.00122874 * t * t * r
            + 0.00085282 * t * r * r
            - 0.00000199 * t * t * r * r
        )
        if r < 13 and 80 <= t <= 112:
            hi_f -= ((13 - r) / 4) * math.sqrt((17 - abs(t - 95.0)) / 17)
        elif r > 85 and 80 <= t <= 87:
            hi_f += ((r - 85) / 10) * ((87 - t) / 5)
    return (hi_f - 32) * 5 / 9


def wbgt_est_c(temp_c: float, rh_pct: float) -> float:
    """HLD §4.2 item 5: WBGT_est = 0.567*T + 0.393*e + 3.94, e in hPa."""
    e = (rh_pct / 100.0) * 6.105 * math.exp(17.27 * temp_c / (237.7 + temp_c))
    return 0.567 * temp_c + 0.393 * e + 3.94


def heat_level(hi_c: float) -> str:
    for upper, level in _HEAT_LEVEL_BANDS:
        if hi_c < upper:
            return level
    return _HEAT_LEVEL_ABOVE


def build_environment_obs(raw: dict) -> dict:
    """raw is an env.v1 payload; returns an EnvironmentObs-shaped dict with the three
    derived fields filled in when temp_c/rh_pct are both known."""
    temp_c, rh_pct = raw.get("temp_c"), raw.get("rh_pct")
    have_both = temp_c is not None and rh_pct is not None
    return {
        "ts": raw["ts"],
        "site_id": raw["site_id"],
        "source": raw["source"],
        "temp_c": temp_c,
        "rh_pct": rh_pct,
        "dew_point_c": dew_point_c(temp_c, rh_pct) if have_both else None,
        "heat_index_c": heat_index_c(temp_c, rh_pct) if have_both else None,
        "wbgt_est_c": wbgt_est_c(temp_c, rh_pct) if have_both else None,
        "precip_mm_h": raw.get("precip_mm_h"),
        "rain_flag": raw.get("rain_flag"),
        "wind_kmh": raw.get("wind_kmh"),
        "gust_kmh": raw.get("gust_kmh"),
        "visibility_m": raw.get("visibility_m"),
        "daylight": None,  # sun-position calc: not built (no rule reads it yet)
        "ground_condition": raw.get("ground_condition"),
    }


_WEATHER = ("temp_c", "rh_pct", "precip_mm_h", "rain_flag", "wind_kmh", "gust_kmh", "visibility_m")


class CurrentEnv:
    """In-memory `current_env[site]`, source-priority resolved (plan.md Phase 2 item 5).

    `ground_condition` is resolved on its own (Phase 5 one-tap `/env/ground`): a ground-only
    obs updates just that field, so a MANUAL tap never freezes the weather at its rank, and
    the resolved ground condition carries over to later weather obs that lack it."""

    # ponytail: MANUAL outranks SIM forever (no expiry); add a max age if a stale manual
    # entry ever shadows a live feed for too long.

    def __init__(self):
        self._by_site: dict[str, dict] = {}
        self._rank_by_site: dict[str, int] = {}
        self._ground: dict[str, tuple[int, str]] = {}  # site -> (rank, ground_condition)

    def update(self, site_id: str, obs: dict) -> None:
        rank = _SOURCE_RANK.get(obs["source"], 0)
        ground = obs.get("ground_condition")
        if ground is not None and rank >= self._ground.get(site_id, (-1, None))[0]:
            self._ground[site_id] = (rank, ground)
        weather = any(obs.get(k) is not None for k in _WEATHER)
        if weather and (site_id not in self._by_site or rank >= self._rank_by_site[site_id]):
            self._by_site[site_id] = obs
            self._rank_by_site[site_id] = rank
        elif site_id not in self._by_site:
            self._by_site[site_id] = obs  # ground-only first obs: nothing better yet
            self._rank_by_site[site_id] = -1  # any weather obs may replace it
        if site_id in self._ground:
            self._by_site[site_id] = {
                **self._by_site[site_id],
                "ground_condition": self._ground[site_id][1],
            }

    def get(self, site_id: str) -> dict | None:
        return self._by_site.get(site_id)
