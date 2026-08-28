"""Unit tests for the haversine routing service (V3 PR7).

Pure math — no Chroma, no LLM, no Google. Pins the great-circle distance against a
known city-pair, the greedy nearest-neighbour ordering, the leg builder, and the
centroid/spread/outlier helpers the constraint layer relies on.
"""

from services.routing.routing_service import (
    centroid,
    distance_km,
    farthest_outlier,
    farthest_pair,
    haversine_km,
    max_pairwise_km,
    order_nearest_neighbour,
    route_legs,
)

# Rough prefecture centroids used across the constraint tests too (kept here as the
# single source for the geo fixtures).
GIFU = (35.8, 137.0)
NAGANO = (36.6, 138.2)
SHIZUOKA = (34.9, 138.4)
OKINAWA = (26.2, 127.7)
AICHI = (35.0, 137.0)


def test_haversine_known_distance_tokyo_osaka():
    # Tokyo (35.68, 139.76) → Osaka (34.69, 135.50) is ~400 km great-circle.
    km = haversine_km(35.68, 139.76, 34.69, 135.50)
    assert 390 <= km <= 415


def test_haversine_zero_distance_is_zero():
    assert haversine_km(35.0, 137.0, 35.0, 137.0) == 0.0


def test_haversine_is_symmetric():
    a = haversine_km(*GIFU, *NAGANO)
    b = haversine_km(*NAGANO, *GIFU)
    assert abs(a - b) < 1e-6


def test_distance_km_wraps_haversine_with_tuples():
    assert abs(distance_km(GIFU, NAGANO) - haversine_km(*GIFU, *NAGANO)) < 1e-9


def test_centroid_is_the_mean_point():
    assert centroid([(0.0, 0.0), (2.0, 4.0)]) == (1.0, 2.0)


def test_centroid_empty_is_none():
    assert centroid([]) is None


def test_order_nearest_neighbour_starts_at_first_and_hops_nearest():
    # Start at A; B is adjacent to A, C is far — so A → B → C regardless of input
    # order of B/C.
    stops = [("A", 35.0, 137.0), ("C", 36.6, 138.2), ("B", 35.1, 137.05)]
    assert order_nearest_neighbour(stops) == ["A", "B", "C"]


def test_order_nearest_neighbour_trivial_sizes():
    assert order_nearest_neighbour([]) == []
    assert order_nearest_neighbour([("A", 1.0, 2.0)]) == ["A"]


def test_route_legs_are_consecutive_pairs_with_distance():
    ordered = [("A", 35.0, 137.0), ("B", 35.1, 137.05), ("C", 36.6, 138.2)]
    legs = route_legs(ordered)
    assert [(l["from"], l["to"]) for l in legs] == [("A", "B"), ("B", "C")]
    assert all(l["distance_km"] > 0 for l in legs)


def test_route_legs_empty_for_single_stop():
    assert route_legs([("A", 1.0, 2.0)]) == []


def test_max_pairwise_km_separates_cluster_from_island():
    mainland = {"Gifu": GIFU, "Nagano": NAGANO, "Shizuoka": SHIZUOKA}
    assert max_pairwise_km(mainland) < 300  # a reachable cluster
    island = {"Okinawa": OKINAWA, "Gifu": GIFU}
    assert max_pairwise_km(island) > 1000  # needs a flight


def test_max_pairwise_km_zero_for_single_region():
    assert max_pairwise_km({"Gifu": GIFU}) == 0.0


def test_farthest_pair_names_the_two_most_distant_regions():
    cen = {"Gifu": GIFU, "Nagano": NAGANO, "Okinawa": OKINAWA}
    pair = farthest_pair(cen)
    assert pair is not None and (
        set(pair) in ({"Okinawa", "Nagano"}, {"Okinawa", "Gifu"})
    )
    # Okinawa must be one end (it is the far outlier).
    assert "Okinawa" in pair


def test_farthest_pair_none_below_two_regions():
    assert farthest_pair({"Gifu": GIFU}) is None


def test_farthest_outlier_picks_the_most_dispersed_region():
    # Of Gifu/Nagano/Shizuoka, Shizuoka has the greatest summed distance to the rest.
    cen = {"Gifu": GIFU, "Nagano": NAGANO, "Shizuoka": SHIZUOKA}
    assert farthest_outlier(cen) == "Shizuoka"


def test_farthest_outlier_none_below_two_regions():
    assert farthest_outlier({"Gifu": GIFU}) is None
