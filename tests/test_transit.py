from backend import transit


def test_subway_stations_are_real_deduplicated_points():
    stations = transit.subway_stations()
    assert len(stations) > 50  # the real GTFS-derived network has well over 50 subway stations
    names = [s[0] for s in stations]
    assert len(names) == len(set(names))  # platforms merged, no "Bloor Station - Eastbound Platform" duplicates
    assert "Bloor Station" in names
    for name, lat, lon in stations:
        assert 43.0 < lat < 44.5  # sanity: inside the Toronto area, not a parsing bug
        assert -80.0 < lon < -78.5
