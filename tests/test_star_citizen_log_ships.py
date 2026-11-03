from voxcomm.skills.star_citizen.gamelog import ships
from voxcomm.skills.star_citizen.gamelog.ships import ShipLogCollector, collect_lines, list_matches, list_names, save_match


def test_collects_unique_raw_classes_and_channels_across_runs(tmp_path):
    path = tmp_path / "ships.sqlite3"
    collect_lines([
        "| CRUS_Spirit_C1_910348951738[910348951738]|CSCItemNavigation::GetRoute|",
        "| CRUS_Spirit_C1_910348951739[910348951739]|CSCItemNavigation::GetRoute|",
        "ClearDriver: Local client node [1] releasing control token for 'ORIG_85X_913108100345' [913108100345]",
        "You have joined channel 'Crusader C1 Spirit : Player'",
        "You have left the channel 'Crusader C1 Spirit : Player'",
        "unrelated line",
    ], path)
    collect_lines(["| ORIG_85X_913108100346[913108100346]|CSCItemNavigation::GetRoute|"], path)
    assert list_names(path) == [
        ("channel", "Crusader C1 Spirit"),
        ("class", "CRUS_Spirit_C1"),
        ("class", "ORIG_85X"),
    ]


def test_empty_collection_does_not_create_database(tmp_path):
    path = tmp_path / "ships.sqlite3"
    collect_lines(["unrelated line"], path)
    assert list_names(path) == []
    assert not path.exists()


def test_matches_preserve_manual_decisions_and_original_names(tmp_path):
    path = tmp_path / "ships.sqlite3"
    collect_lines(["| CRUS_Spirit_C1_123456[123456]|CSCItemNavigation::GetRoute|"], path)
    suggestion = {"id": 4, "name": "C1 Spirit", "manufacturer": "Crusader Industries"}
    corrected = {"id": 5, "name": "C1", "manufacturer": "Crusader Industries"}
    save_match("CRUS_Spirit_C1", suggestion, "llm", path)
    save_match("CRUS_Spirit_C1", corrected, "manual", path)
    save_match("CRUS_Spirit_C1", suggestion, "llm", path)
    save_match("UNKNOWN", None, "llm", path)
    assert list_matches(path)["CRUS_Spirit_C1"] == {**corrected, "source": "manual"}
    assert list_matches(path)["UNKNOWN"]["id"] is None
    assert list_names(path) == [("class", "CRUS_Spirit_C1")]
    ships.retry_unidentified(path)
    assert "UNKNOWN" not in list_matches(path)
    assert list_matches(path)["CRUS_Spirit_C1"]["source"] == "manual"


def test_scans_complete_log_incrementally_and_handles_replacement(tmp_path, monkeypatch):
    path = tmp_path / "Game.log"
    storage = tmp_path / "ships.sqlite3"
    first = "| CRUS_Spirit_C1_123456[123456]|CSCItemNavigation::GetRoute|\n"
    path.write_text("x" * 80 + "\n" + first, encoding="utf-8")
    monkeypatch.setattr(ships, "SCAN_BYTES", 64)
    collector = ShipLogCollector(storage)
    for _ in range(4):
        collector.scan(path)
    assert list_names(storage) == [("class", "CRUS_Spirit_C1")]
    with path.open("a", encoding="utf-8") as handle:
        handle.write("You have joined channel 'Origin 85X : Player'")
    collector.scan(path)
    assert len(list_names(storage)) == 1
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    collector.scan(path)
    assert ("channel", "Origin 85X") in list_names(storage)
    replacement = tmp_path / "replacement"
    replacement.write_text("| ORIG_85X_123457[123457]|CSCItemNavigation::GetRoute|\n", encoding="utf-8")
    replacement.replace(path)
    collector.scan(path)
    assert ("class", "ORIG_85X") in list_names(storage)