from backend import ingest


def _feed_bytes(alerts: list[dict]) -> bytes:
    """Build a minimal real GTFS-RT FeedMessage with one Alert entity per dict in `alerts`.
    Each dict: {cause, effect, header, routes: [route_id, ...]}."""
    from google.transit import gtfs_realtime_pb2 as pb

    f = pb.FeedMessage()
    f.header.gtfs_realtime_version = "2.0"
    for i, a in enumerate(alerts):
        e = f.entity.add()
        e.id = str(i)
        al = e.alert
        if a.get("cause") is not None:
            al.cause = a["cause"]
        if a.get("effect") is not None:
            al.effect = a["effect"]
        if a.get("header"):
            al.header_text.translation.add(text=a["header"], language="en")
        if a.get("description"):
            al.description_text.translation.add(text=a["description"], language="en")
        for r in a.get("routes", []):
            al.informed_entity.add(route_id=r)
    return f.SerializeToString()


def test_parse_alerts_reads_cause_effect_text_per_route():
    from google.transit import gtfs_realtime_pb2 as pb

    raw = _feed_bytes([
        {"cause": pb.Alert.CONSTRUCTION, "effect": pb.Alert.DETOUR, "header": "Streetcars detoured",
         "description": "Construction at King and Spadina", "routes": ["504", "510"]},
    ])
    out = ingest.parse_alerts(raw)
    assert {r["route_id"] for r in out} == {"504", "510"}
    row = next(r for r in out if r["route_id"] == "504")
    assert row["cause"] == "CONSTRUCTION"
    assert row["effect"] == "DETOUR"
    assert row["header_text"] == "Streetcars detoured"
    assert row["description_text"] == "Construction at King and Spadina"


def test_parse_alerts_skips_entities_with_no_routes():
    from google.transit import gtfs_realtime_pb2 as pb

    raw = _feed_bytes([{"cause": pb.Alert.WEATHER, "effect": pb.Alert.SIGNIFICANT_DELAYS, "header": "Storm", "routes": []}])
    assert ingest.parse_alerts(raw) == []


def test_parse_alerts_on_an_empty_feed():
    assert ingest.parse_alerts(_feed_bytes([])) == []


def test_store_raw_inserts_alerts_alongside_ttc_and_bikes(monkeypatch):
    import base64
    import time

    from google.transit import gtfs_realtime_pb2 as pb

    trips = pb.FeedMessage()
    trips.header.gtfs_realtime_version = "2.0"
    alerts_bin = _feed_bytes([{"cause": pb.Alert.CONSTRUCTION, "effect": pb.Alert.DETOUR, "header": "Detour", "routes": ["504"]}])

    calls = {}
    monkeypatch.setattr(ingest.store, "insert_ttc", lambda *a, **k: calls.setdefault("ttc", (a, k)))
    monkeypatch.setattr(ingest.store, "insert_bike", lambda *a, **k: calls.setdefault("bike", (a, k)))
    monkeypatch.setattr(ingest.store, "insert_ttc_alerts", lambda *a, **k: calls.setdefault("alerts", (a, k)))

    raw = {
        "ts": time.time(),
        "gbfs": {"data": {"stations": []}},
        "trips_b64": base64.b64encode(trips.SerializeToString()).decode(),
        "alerts_b64": base64.b64encode(alerts_bin).decode(),
    }
    ingest.store_raw(raw, "live")
    assert "alerts" in calls
    inserted = calls["alerts"][0][1]  # positional args: (ts, alerts)
    assert inserted[0]["route_id"] == "504" and inserted[0]["header_text"] == "Detour"
