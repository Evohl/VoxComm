import httpx
import pytest
import sqlite3

from voxcomm.skills.star_citizen.database import RESOURCES, _request_resource, last_updated, read_resource, read_snapshot, refresh, refresh_due, replace_snapshot


def test_read_transaction_keeps_one_consistent_snapshot(tmp_path):
    path = tmp_path / "uex.sqlite3"
    replace_snapshot({"vehicles": [{"name": "Old"}]}, path, now=100)
    with read_snapshot(path) as snapshot:
        assert snapshot.last_updated() == 100
        with sqlite3.connect(path, timeout=0) as writer:
            writer.execute("UPDATE records SET data = ?", ('{"name": "New"}',))
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                writer.commit()
            writer.rollback()
        assert snapshot.read_resource("vehicles") == [{"name": "Old"}]


@pytest.mark.parametrize("resource", ["items", "items_attributes"])
def test_empty_category_response_can_have_null_data(resource):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok", "data": None}))
    with httpx.Client(transport=transport) as client:
        assert _request_resource(client, resource, {"id_category": 12}) == []
        with pytest.raises(ValueError):
            _request_resource(client, "vehicles")
        with pytest.raises(ValueError):
            _request_resource(client, resource)


def test_snapshot_is_replaced_atomically_and_tracks_daily_refresh(tmp_path):
    path = tmp_path / "uex.sqlite3"
    assert refresh_due(path, now=100)
    replace_snapshot({"vehicles": [{"name": "C1 Spirit"}]}, path, now=100)
    assert read_resource("vehicles", path) == [{"name": "C1 Spirit"}]
    assert not refresh_due(path, now=99)
    assert not refresh_due(path, now=100 + 86399)
    assert refresh_due(path, now=100 + 86400)

    with pytest.raises(TypeError):
        replace_snapshot({"vehicles": [{"name": object()}]}, path, now=200)
    assert last_updated(path) == 100
    assert read_resource("vehicles", path) == [{"name": "C1 Spirit"}]

    replace_snapshot({"items": [{"name": "Cannon"}]}, path, now=300)
    assert last_updated(path) == 300
    assert read_resource("vehicles", path) == []
    assert read_resource("items", path) == [{"name": "Cannon"}]


def test_full_import_respects_daily_schedule_and_preserves_previous_snapshot(tmp_path):
    path = tmp_path / "uex.sqlite3"
    calls = []

    def request(resource, params=None):
        calls.append((resource, params))
        if resource == "categories":
            return [{"id": 32, "type": "item"}, {"id": 39, "type": "service"}]
        return [{"id": 1, "name": resource}]

    assert refresh(path, request=request, now=100)
    assert len(calls) == len(RESOURCES) + 2
    assert ("items", {"id_category": 32}) in calls
    assert ("items_attributes", {"id_category": 32}) in calls
    assert not refresh(path, request=request, now=101)
    assert len(calls) == len(RESOURCES) + 2

    def broken_request(resource, params=None):
        if resource == "items_attributes":
            raise RuntimeError("network failure")
        return request(resource, params)

    with pytest.raises(RuntimeError):
        refresh(path, request=broken_request, now=100 + 86400)
    assert last_updated(path) == 100
    assert read_resource("items", path) == [{"id": 1, "name": "items"}]