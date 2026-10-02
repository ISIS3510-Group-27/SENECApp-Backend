import math
from collections.abc import Iterable
from typing import Protocol

EARTH_RADIUS_M = 6_371_000
# Average walking speed used to turn distances into "minutes away".
WALKING_SPEED_M_PER_MIN = 80


class HasCoordinates(Protocol):
    latitude: float
    longitude: float


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two WGS84 points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def nearest[T: HasCoordinates](
    latitude: float, longitude: float, places: Iterable[T], max_distance_m: float | None = None
) -> tuple[T, float] | None:
    """Closest place and its distance, or ``None`` if none is within ``max_distance_m``."""
    best: tuple[T, float] | None = None
    for place in places:
        distance = haversine_m(latitude, longitude, place.latitude, place.longitude)
        if best is None or distance < best[1]:
            best = (place, distance)
    if best is None or (max_distance_m is not None and best[1] > max_distance_m):
        return None
    return best


def walking_minutes(distance_m: float) -> int:
    return max(1, round(distance_m / WALKING_SPEED_M_PER_MIN))
