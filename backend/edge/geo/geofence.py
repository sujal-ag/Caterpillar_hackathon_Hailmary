"""Hazard geofencing (HLD §4.12; plan.md Phase 5 item 6). Pure: pins + position in, zone
entries/exits out. Geometry is local site metres (hazards.v1 `LocalGeometry`).

Enter when the machine is inside the zone (a point pin: within `radius_m`; a polygon: covered).
Exit only once it is more than `hysteresis_m` outside, so a machine jittering on the edge
enters once (R14 `exit_hysteresis_m`)."""

from shapely.geometry import Point, shape


def outside_by(pin: dict, x: float, y: float) -> float:
    """Metres outside the zone boundary (<= 0 = inside)."""
    geom = pin["geometry"]
    p = Point(x, y)
    if geom["type"] == "Point":
        return p.distance(Point(*geom["coordinates"][:2])) - pin["radius_m"]
    return shape(geom).distance(p)  # 0 when inside the polygon


def update(
    zones_inside: dict, pins: dict, x: float, y: float, hysteresis_m: float
) -> tuple[list[str], list[str]]:
    """(entered pin_ids, exited pin_ids) for a machine at (x, y). `zones_inside` and `pins`
    are keyed by pin_id; neither is mutated."""
    entered, exited = [], []
    for pin_id, pin in pins.items():
        d = outside_by(pin, x, y)
        if pin_id in zones_inside:
            if d > hysteresis_m:
                exited.append(pin_id)
        elif d <= 0:
            entered.append(pin_id)
    return entered, exited
