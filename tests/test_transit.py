from backend import transit


def test_interchange_stations_are_real_deduplicated_points():
    stations = transit.interchange_stations()
    assert len(stations) > 50  # the real GTFS-derived network has well over 50 subway + GO stations
    names = [s[0] for s in stations]
    assert len(names) == len(set(names))  # platforms/directions merged, no duplicate station names
    assert "Bloor Station" in names
    assert "Union Station GO" in names  # GO stations are in the pool too, not just TTC subway
    kinds = {s[3] for s in stations}
    assert kinds == {"subway", "go_train"}
    for name, lat, lon, kind in stations:
        assert 42.5 < lat < 44.5  # sanity: inside the GO-served region, not a parsing bug
        assert -81.0 < lon < -78.5
        assert kind in ("subway", "go_train")
