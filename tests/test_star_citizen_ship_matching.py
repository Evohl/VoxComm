import json
from unittest.mock import patch

from voxcomm.skills.star_citizen import database
from voxcomm.skills.star_citizen.gamelog import ships
from voxcomm.config import AppConfig
from voxcomm.skills.star_citizen.gamelog.ship_matching import ShipMatchWorker, candidates, matching_profile, suggest_pending


def test_candidate_ranking_prioritizes_matching_ship():
    vehicles = [
        {"id": 4, "name": "C1 Spirit", "company_name": "Crusader Industries"},
        {"id": 5, "name": "85X", "company_name": "Origin Jumpworks"},
    ]
    options = candidates("CRUS_Spirit_C1", vehicles)
    assert options[0] == {"id": 4, "name": "C1 Spirit", "manufacturer": "Crusader Industries"}


def test_automatic_matching_requires_both_permissions_and_model_on_same_existing_profile():
    config = AppConfig(special_prompts={"Log": "", "UEX": "", "Both": ""},
                       profile_models={"Log": "model", "UEX": "model", "Both": "model", "Deleted": "model"},
                       enabled_tools={"Log": ["star_citizen_log"], "UEX": ["star_citizen_uex"],
                                      "Deleted": ["star_citizen"]})
    assert matching_profile(config) is None
    config.enabled_tools["Both"] = ["star_citizen_log", "star_citizen_uex"]
    assert matching_profile(config) == "Both"


def test_candidate_list_is_bounded_and_excludes_incomplete_records():
    vehicles = [{"id": number, "name": f"Ship {number}", "company_name": "Maker"} for number in range(50)]
    vehicles.extend([{"id": True, "name": "Fake", "company_name": "Maker"}, {"id": 99, "name": "No maker"}])
    options = candidates("TEST_Ship_3", vehicles)
    assert len(options) == 30
    assert options[0]["id"] == 3
    assert all(type(option["id"]) is int and option["manufacturer"] == "Maker" for option in options)


def test_suggestions_use_snapshot_and_reject_invented_or_unoffered_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    database.replace_snapshot({"vehicles": [
        {"id": 4, "name": "C1 Spirit", "company_name": "Crusader Industries"},
        {"id": 5, "name": "85X", "company_name": "Origin Jumpworks"},
    ]})
    ships.collect_lines([
        "| CRUS_Spirit_C1_123456[123456]|CSCItemNavigation::GetRoute|",
        "| ORIG_85X_123456[123456]|CSCItemNavigation::GetRoute|",
    ])

    def request(messages):
        choices = json.loads(messages[-1]["content"])
        assert set(choices) == {"CRUS_Spirit_C1", "ORIG_85X"}
        return {"content": json.dumps([
            {"log_name": "CRUS_Spirit_C1", "vehicle_id": 4},
            {"log_name": "ORIG_85X", "vehicle_id": 999},
            {"log_name": "FORGED", "vehicle_id": 5},
        ])}

    assert suggest_pending(request) == 1
    assert ships.list_matches() == {"CRUS_Spirit_C1": {
        "id": 4, "name": "C1 Spirit", "manufacturer": "Crusader Industries", "source": "llm",
    }}
    assert suggest_pending(lambda messages: {"content": '[{"log_name":"ORIG_85X","vehicle_id":null}]'}) == 1
    assert ships.list_matches()["ORIG_85X"]["id"] is None


def test_permission_revocation_discards_in_flight_model_results(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    database.replace_snapshot({"vehicles": [{"id": 1, "name": "85X", "company_name": "Origin"}]})
    ships.collect_lines(["| ORIG_85X_123456[123456]|CSCItemNavigation::GetRoute|"])
    allowed = [True]

    def request(messages):
        allowed[0] = False
        return {"content": '[{"log_name":"ORIG_85X","vehicle_id":1}]'}

    assert suggest_pending(request, lambda: allowed[0]) == 0
    assert ships.list_matches() == {}


def test_worker_uses_the_authorized_profiles_configured_model(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    database.replace_snapshot({"vehicles": [{"id": 1, "name": "85X", "company_name": "Origin"}]})
    ships.collect_lines(["| ORIG_85X_123456[123456]|CSCItemNavigation::GetRoute|"])
    config = AppConfig(special_prompts={"Game": ""}, profile_models={"Game": "model"},
                       enabled_tools={"Game": ["star_citizen_log", "star_citizen_uex"]},
                       llm_provider="ollama", llm_url="http://localhost:11434/")
    worker = ShipMatchWorker()
    with patch("voxcomm.providers.llm._request", return_value={
        "content": '[{"log_name":"ORIG_85X","vehicle_id":1}]',
    }) as request:
        worker.start_if_due(lambda: config)
        assert worker._lock.acquire(timeout=3)
        worker._lock.release()
    assert request.call_args.args[:3] == ("ollama", "http://localhost:11434", "model")
    assert ships.list_matches()["ORIG_85X"]["name"] == "85X"