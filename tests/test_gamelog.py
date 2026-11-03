import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voxcomm.gamelog import GameLogTracker
from voxcomm.gamelog.facts import (
    NAV_TARGET_SET, PILOT_LEFT, PILOT_TAKEN, QUANTUM_ARRIVED, QUANTUM_SIGNATURE_UNKNOWN, SHIP_ENTERED, SHIP_LEFT, GameFacts,
)
from voxcomm.gamelog.paths import log_path_from_executable
from voxcomm.gamelog.process import find_game_executable
from voxcomm.gamelog.reader import LogReader, RecentLogReader

LOCATION_LINE = "<X> requested inventory for Location[Nyx_Levski] of ship"
STREAMING_SOC_LINE = (
    "<InitializeSlotMetadata_Success> Harvestable Slot Host "
    "[StreamingSOC_levski_v2_ext][875757921510] has successfully found metadata for [34] slots."
)
DATABASE_VERSION_LINE = (
    "<2026-11-01T10:27:27.840Z> [Notice] <SetDatabaseVersion> "
    "Database version: sc-alpha-4.10.2-12881860 [Team_OnlineTech][EntityGraph]"
)
JOIN_LINE = "<SHUDEvent_OnNotification> You have joined channel 'Crusader C1 Spirit : Vic_Evohl_Morrison'."
LEAVE_LINE = "<SHUDEvent_OnNotification> You have left the channel 'Crusader C1 Spirit : Vic_Evohl_Morrison'."
ROUTE_LINE = (
    "<Failed to get starmap route data!> [ItemNavigation][CL][1312] | NOT AUTH | "
    "CRUS_Spirit_C1_910348951738[910348951738]|CSCItemNavigation::GetStarmapRouteSegmentData|"
    "No Route loaded! [Team_CGP4][QuantumTravel]"
)
EXIT_LINE = (
    "<Vehicle Control Flow> CVehicleMovementBase::ClearDriver: Local client node [204803092849] "
    "releasing control token for 'CRUS_Spirit_C1_910348951738' [910348951738]"
)
QUANTUM_SELECT_LINE = (
    "<Player Selected Quantum Target - Local> [ItemNavigation][CL][1312] | NOT AUTH | "
    "CRUS_Spirit_C1_910348951738[910348951738]|CSCItemNavigation::OnPlayerSelectedQuantumTarget|"
    "Player has selected point glaciemring_TransitPoint_Bravo as their destination, routing locally [Team_CGP4][QuantumTravel]"
)
QUANTUM_ROUTE_LINE = (
    "<Calculate Route> [ItemNavigation][CL][1312] | NOT AUTH | CRUS_Spirit_C1_910348951738[910348951738]|"
    "CSCItemNavigation::CalculateRoute|Successfully calculated route to glaciemring_TransitPoint_Bravo fuel estimate 410808.375000 [Team_CGP4][QuantumTravel]"
)
QUANTUM_ARRIVED_LINE = (
    "<Quantum Drive Arrived> [ItemNavigation][CL][1312] | NOT AUTH | CRUS_Spirit_C1_910348951738[910348951738]|"
    "CSCItemNavigation::OnQuantumDriveArrived|Quantum Drive has arrived at final destination [Team_CGP4][QuantumTravel]"
)
GAME_EXE = "Z:\\mnt\\Lexar4TB\\star-citizen\\drive_c\\Program Files\\Roberts Space Industries\\StarCitizen\\LIVE\\Bin64\\StarCitizen.exe"


class PathTests(unittest.TestCase):
    def test_maps_wine_path_to_game_log_next_to_bin64(self):
        self.assertEqual(
            log_path_from_executable(GAME_EXE),
            Path("/mnt/Lexar4TB/star-citizen/drive_c/Program Files/Roberts Space Industries/StarCitizen/LIVE/Game.log"),
        )

    def test_rejects_non_wine_and_launcher_paths(self):
        self.assertIsNone(log_path_from_executable("/usr/bin/python"))
        self.assertIsNone(log_path_from_executable("Z:\\mnt\\x\\StarCitizen_Launcher.exe"))


class ProcessTests(unittest.TestCase):
    def test_finds_game_executable_and_skips_launcher(self):
        with tempfile.TemporaryDirectory() as root:
            proc = Path(root)
            (proc / "100").mkdir()
            (proc / "100" / "cmdline").write_bytes(b"Z:\\x\\Bin64\\StarCitizen_Launcher.exe\0")
            (proc / "200").mkdir()
            (proc / "200" / "cmdline").write_bytes(f"{GAME_EXE}\0-noplay\0".encode())
            (proc / "self").mkdir()
            self.assertEqual(find_game_executable(proc), GAME_EXE)

    def test_returns_none_when_game_is_not_running(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(find_game_executable(Path(root)))


class FactsTests(unittest.TestCase):
    def test_unknown_signatures_keep_each_ship_type(self):
        facts = GameFacts()
        origin_arrival = QUANTUM_ARRIVED_LINE.replace("CRUS_Spirit_C1_910348951738", "ORIG_85X_913108100345").replace("[910348951738]", "[913108100345]")
        events = facts.apply([QUANTUM_ARRIVED_LINE, origin_arrival])
        self.assertEqual(events, [QUANTUM_SIGNATURE_UNKNOWN, QUANTUM_SIGNATURE_UNKNOWN])
        self.assertEqual([event.ship for event in events], ["Crusader Spirit C1", "Origin 85X"])

    def test_unknown_quantum_signature_preserves_own_navigation(self):
        facts = GameFacts()
        self.assertEqual(facts.apply([QUANTUM_ARRIVED_LINE]), [QUANTUM_SIGNATURE_UNKNOWN])
        facts.apply([JOIN_LINE, EXIT_LINE, QUANTUM_ROUTE_LINE])
        foreign_arrival = QUANTUM_ARRIVED_LINE.replace("910348951738", "910348951739")
        self.assertEqual(facts.apply([foreign_arrival]), [QUANTUM_SIGNATURE_UNKNOWN])
        self.assertEqual(facts.nav_target, "glaciemring_TransitPoint_Bravo")
        self.assertEqual(facts.apply([QUANTUM_ARRIVED_LINE]), [QUANTUM_ARRIVED])
        self.assertEqual(facts.apply(["Quantum Drive has arrived at final destination"]), [])

    def test_join_route_and_exit_update_state(self):
        facts = GameFacts()
        facts.apply([LOCATION_LINE, JOIN_LINE])
        self.assertEqual((facts.location, facts.ship, facts.in_ship, facts.pilot_seat), ("Nyx_Levski", "Crusader C1 Spirit", True, None))

        facts.apply([EXIT_LINE, ROUTE_LINE])
        self.assertIs(facts.pilot_seat, True)

        facts.apply([EXIT_LINE])
        self.assertEqual((facts.ship, facts.pilot_seat), ("Crusader C1 Spirit", False))

    def test_leave_only_changes_in_ship(self):
        facts = GameFacts()
        facts.apply([JOIN_LINE, EXIT_LINE, ROUTE_LINE, LEAVE_LINE])
        self.assertEqual((facts.ship, facts.in_ship, facts.pilot_seat), ("Crusader C1 Spirit", False, False))

    def test_exit_without_join_uses_vehicle_name(self):
        facts = GameFacts()
        facts.apply([EXIT_LINE])
        self.assertEqual((facts.ship, facts.in_ship, facts.pilot_seat), ("CRUS_Spirit_C1", None, False))
        self.assertEqual(facts.ship_instance, "910348951738")

    def test_only_local_exit_establishes_ship_instance(self):
        facts = GameFacts()
        facts.apply([EXIT_LINE.replace("Local client node", "Remote client node")])
        self.assertEqual((facts.ship_instance, facts.pilot_seat), ("", None))
        facts.apply([EXIT_LINE])
        self.assertEqual(facts.ship_instance, "910348951738")

    def test_same_model_with_different_channel_owner_clears_previous_instance(self):
        facts = GameFacts()
        facts.apply([JOIN_LINE, EXIT_LINE, ROUTE_LINE, QUANTUM_ROUTE_LINE])
        self.assertTrue(facts.pilot_seat)
        facts.apply([JOIN_LINE.replace("Vic_Evohl_Morrison", "Another_Player")])
        self.assertEqual((facts.ship_instance, facts.pilot_seat, facts.nav_target), ("", None, ""))
        self.assertEqual(facts.apply([QUANTUM_ARRIVED_LINE]), [QUANTUM_SIGNATURE_UNKNOWN])

    def test_navigation_requires_local_ship_instance(self):
        facts = GameFacts()
        self.assertEqual(facts.apply([JOIN_LINE, QUANTUM_ROUTE_LINE, QUANTUM_ARRIVED_LINE]), [SHIP_ENTERED, QUANTUM_SIGNATURE_UNKNOWN])
        self.assertEqual((facts.nav_target, facts.pilot_seat), ("", None))
        facts.apply([EXIT_LINE])
        self.assertEqual(facts.apply([QUANTUM_ROUTE_LINE]), [NAV_TARGET_SET])
        other_route = QUANTUM_ROUTE_LINE.replace("910348951738", "910348951739")
        other_arrival = QUANTUM_ARRIVED_LINE.replace("910348951738", "910348951739")
        other_no_route = ROUTE_LINE.replace("910348951738", "910348951739")
        self.assertEqual(facts.apply([other_route, other_arrival, other_no_route]), [QUANTUM_SIGNATURE_UNKNOWN])
        self.assertEqual(facts.nav_target, "glaciemring_TransitPoint_Bravo")
        self.assertIs(facts.pilot_seat, False)
        self.assertEqual(facts.apply([QUANTUM_ARRIVED_LINE]), [QUANTUM_ARRIVED])
        facts.apply([LEAVE_LINE, JOIN_LINE])
        self.assertEqual(facts.ship_instance, "")
        self.assertEqual(facts.apply([QUANTUM_ROUTE_LINE, QUANTUM_ARRIVED_LINE]), [QUANTUM_SIGNATURE_UNKNOWN])

    def test_apply_reports_state_transitions_once(self):
        facts = GameFacts()
        self.assertEqual(facts.apply([JOIN_LINE]), [SHIP_ENTERED])
        self.assertEqual(facts.apply([JOIN_LINE]), [])
        self.assertEqual(facts.apply([LEAVE_LINE]), [SHIP_LEFT])
        facts.apply([JOIN_LINE, EXIT_LINE])
        self.assertEqual(facts.apply([ROUTE_LINE]), [PILOT_TAKEN])
        self.assertEqual(facts.apply([ROUTE_LINE]), [])
        self.assertEqual(facts.apply([EXIT_LINE]), [PILOT_LEFT])

    def test_new_nav_target_and_arrival_are_reported(self):
        facts = GameFacts()
        facts.apply([JOIN_LINE, EXIT_LINE])
        self.assertEqual(facts.apply([QUANTUM_ROUTE_LINE]), [NAV_TARGET_SET])
        self.assertEqual(facts.apply([QUANTUM_ROUTE_LINE]), [])
        self.assertEqual(facts.apply([QUANTUM_ARRIVED_LINE]), [QUANTUM_ARRIVED])

    def test_quantum_target_comes_from_explicit_selection_only(self):
        facts = GameFacts()
        facts.apply([JOIN_LINE, EXIT_LINE])
        facts.apply([QUANTUM_ROUTE_LINE])
        self.assertEqual(facts.quantum_target, "")

        facts.apply([QUANTUM_SELECT_LINE])
        self.assertEqual(facts.quantum_target, "glaciemring_TransitPoint_Bravo")
        self.assertIn("Quantenziel: glaciemring_TransitPoint_Bravo", facts.describe())

    def test_describe_marks_unknown_values(self):
        text = GameFacts().describe()
        self.assertIn("Spielversion: unbekannt", text)
        self.assertIn("Game-Build: unbekannt", text)
        self.assertIn("Ungefähre Region (Streaming-SOC): unbekannt", text)
        self.assertIn("Letzter bekannter Aufenthalt (Inventory-Location): unbekannt", text)
        self.assertIn("Im Schiff: unbekannt", text)
        self.assertIn("Pilotensitz: unbekannt", text)
        self.assertIn("Quantenziel: unbekannt", text)
        self.assertIn("Geplantes Navziel: unbekannt", text)
        self.assertIn("QT-Treibstoffschätzung: unbekannt", text)

    def test_reads_game_version_and_build_from_database_version_line(self):
        facts = GameFacts()
        facts.apply([DATABASE_VERSION_LINE])

        self.assertEqual((facts.game_version, facts.game_build), ("4.10.2", "12881860"))
        self.assertIn("Spielversion: 4.10.2", facts.describe())
        self.assertIn("Game-Build: 12881860", facts.describe())

    def test_route_calculation_sets_planned_nav_target_and_fuel(self):
        facts = GameFacts()
        facts.apply([JOIN_LINE, EXIT_LINE])
        facts.apply([QUANTUM_ROUTE_LINE])
        self.assertEqual((facts.nav_target, facts.quantum_fuel), ("glaciemring_TransitPoint_Bravo", "410808.375000"))
        self.assertIn("Geplantes Navziel: glaciemring_TransitPoint_Bravo", facts.describe())
        self.assertIn("QT-Treibstoffschätzung: 410808.375000", facts.describe())

    def test_arrival_makes_nav_target_suspected_until_location_is_confirmed(self):
        facts = GameFacts()
        facts.apply([LOCATION_LINE, JOIN_LINE, EXIT_LINE, QUANTUM_ROUTE_LINE, QUANTUM_ARRIVED_LINE])
        self.assertEqual((facts.nav_target, facts.quantum_fuel), ("", ""))
        self.assertEqual(facts.location, "Nyx_Levski")
        self.assertIn("Letzter bekannter Aufenthalt (Inventory-Location): Nyx_Levski", facts.describe())

        facts.apply(["<X> requested inventory for Location[glaciemring_TransitPoint_Bravo] of ship"])
        self.assertEqual(facts.location, "glaciemring_TransitPoint_Bravo")

    def test_streaming_soc_is_approximate_region_not_inventory_location(self):
        facts = GameFacts()
        facts.apply([STREAMING_SOC_LINE])
        self.assertEqual(facts.streaming_region, "StreamingSOC_levski_v2_ext")
        self.assertEqual(facts.location, "")

        facts.apply([LOCATION_LINE, QUANTUM_ROUTE_LINE, QUANTUM_ARRIVED_LINE])
        self.assertEqual(facts.streaming_region, "StreamingSOC_levski_v2_ext")
        self.assertEqual(facts.location, "Nyx_Levski")
        self.assertIn("Ungefähre Region (Streaming-SOC): StreamingSOC_levski_v2_ext", facts.describe())
        self.assertIn("Letzter bekannter Aufenthalt (Inventory-Location): Nyx_Levski", facts.describe())


class ReaderTests(unittest.TestCase):
    def test_recent_reader_bounds_bytes_and_recovers_partial_utf8_lines(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "Game.log"
            log.write_bytes(b"discarded\nfirst\nsecond\n")
            with patch("voxcomm.gamelog.reader.RECENT_LOG_MAX_BYTES", 16):
                reader = RecentLogReader(log, max_lines=10)
                self.assertEqual(reader.read_new_lines(), (True, ["first", "second"]))
            with log.open("ab") as handle:
                handle.write("Grü".encode("utf-8")[:-1])
            self.assertEqual(reader.read_new_lines(), (False, []))
            with log.open("ab") as handle:
                handle.write("Grü".encode("utf-8")[-1:] + b"n\n")
            self.assertEqual(reader.read_new_lines(), (False, ["Grün"]))

    def test_recent_reader_bounds_existing_lines_and_follows_partial_updates(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "Game.log"
            log.write_text("".join(f"line {number}\n" for number in range(10050)) + "part", encoding="utf-8")
            reader = RecentLogReader(log)
            reset, lines = reader.read_new_lines()
            self.assertTrue(reset)
            self.assertEqual(len(lines), 10000)
            self.assertEqual((lines[0], lines[-1]), ("line 50", "line 10049"))
            with log.open("a", encoding="utf-8") as handle:
                handle.write("ial\nnext\n")
            self.assertEqual(reader.read_new_lines(), (False, ["partial", "next"]))

    def test_recent_reader_resets_after_truncation_or_replacement(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "Game.log"
            log.write_text("old line\n", encoding="utf-8")
            reader = RecentLogReader(log)
            self.assertEqual(reader.read_new_lines(), (True, ["old line"]))
            log.write_text("new\n", encoding="utf-8")
            self.assertEqual(reader.read_new_lines(), (True, ["new"]))
            replacement = Path(root) / "replacement"
            replacement.write_text("replacement line\n", encoding="utf-8")
            replacement.replace(log)
            self.assertEqual(reader.read_new_lines(), (True, ["replacement line"]))

    def test_returns_only_new_complete_lines(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "Game.log"
            log.write_text("first\nsecond\n", encoding="utf-8")
            reader = LogReader(log)
            self.assertEqual(reader.read_new_lines(), ["first", "second"])

            with log.open("a", encoding="utf-8") as handle:
                handle.write("third\nfour")
            self.assertEqual(reader.read_new_lines(), ["third"])

            with log.open("a", encoding="utf-8") as handle:
                handle.write("th\n")
            self.assertEqual(reader.read_new_lines(), ["fourth"])

    def test_restarts_after_truncation(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "Game.log"
            log.write_text("old line\n", encoding="utf-8")
            reader = LogReader(log)
            reader.read_new_lines()
            log.write_text("new\n", encoding="utf-8")
            self.assertEqual(reader.read_new_lines(), ["new"])

    def test_initial_tail_drops_partial_first_line(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "Game.log"
            log.write_text("cut-off-line\nkept\n", encoding="utf-8")
            reader = LogReader(log, initial_tail_bytes=len("ine\nkept\n"))
            self.assertEqual(reader.read_new_lines(), ["kept"])

    def test_missing_file_returns_nothing(self):
        self.assertEqual(LogReader(Path("/nonexistent/Game.log")).read_new_lines(), [])


class TrackerTests(unittest.TestCase):
    def test_log_replacement_and_truncation_forget_old_ship_instance(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "Game.log"
            log.write_text(JOIN_LINE + "\n" + EXIT_LINE + "\n", encoding="utf-8")
            executable = f"Z:{Path(root).as_posix()}/Bin64/StarCitizen.exe"
            tracker = GameLogTracker(executable_finder=lambda: executable)
            tracker.poll()
            self.assertEqual(tracker.facts.ship_instance, "910348951738")
            tracker.set_ship_connection(True)
            replacement = Path(root) / "replacement"
            replacement.write_text(JOIN_LINE + "\n" + QUANTUM_ARRIVED_LINE + "\n", encoding="utf-8")
            replacement.replace(log)
            tracker.poll()
            self.assertEqual(tracker.facts.ship_instance, "")
            self.assertEqual(tracker.connected_ship, (False, ""))
            self.assertEqual(tracker.take_events(), [])
            with log.open("a", encoding="utf-8") as handle:
                handle.write(EXIT_LINE + "\n")
            tracker.poll()
            self.assertEqual(tracker.facts.ship_instance, "910348951738")
            log.write_text(JOIN_LINE + "\n", encoding="utf-8")
            tracker.poll()
            self.assertEqual(tracker.facts.ship_instance, "")
            self.assertEqual(tracker.take_events(), [])

    def test_no_context_when_game_is_not_running(self):
        tracker = GameLogTracker(executable_finder=lambda: None)
        self.assertEqual(tracker.prompt_context(), "")

    def test_context_follows_log_growth(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "Game.log"
            log.write_text("", encoding="utf-8")
            executable = f"Z:{Path(root).as_posix()}/Bin64/StarCitizen.exe"
            tracker = GameLogTracker(executable_finder=lambda: executable)
            self.assertIn("Im Schiff: unbekannt", tracker.prompt_context())
            self.assertEqual(tracker.path, log)

            log.write_text(LOCATION_LINE + "\n" + JOIN_LINE + "\n", encoding="utf-8")
            text = tracker.prompt_context()
            self.assertIn("Letzter bekannter Aufenthalt (Inventory-Location): Nyx_Levski", text)
            self.assertIn("Schiff: Crusader C1 Spirit", text)
            self.assertIn("Im Schiff: ja", text)

    def test_version_is_loaded_from_log_header_outside_initial_tail(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "Game.log"
            log.write_text(DATABASE_VERSION_LINE + "\n" + ("filler\n" * 40000) + STREAMING_SOC_LINE + "\n", encoding="utf-8")
            executable = f"Z:{Path(root).as_posix()}/Bin64/StarCitizen.exe"
            tracker = GameLogTracker(executable_finder=lambda: executable)

            text = tracker.prompt_context()

            self.assertIn("Spielversion: 4.10.2", text)
            self.assertIn("Game-Build: 12881860", text)
            self.assertIn("Ungefähre Region (Streaming-SOC): StreamingSOC_levski_v2_ext", text)

    def test_existing_log_content_is_not_announced(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "Game.log"
            log.write_text(JOIN_LINE + "\n", encoding="utf-8")
            executable = f"Z:{Path(root).as_posix()}/Bin64/StarCitizen.exe"
            tracker = GameLogTracker(executable_finder=lambda: executable)
            tracker.poll()
            self.assertEqual(tracker.take_events(), [])
            self.assertIs(tracker.facts.in_ship, True)

            with log.open("a", encoding="utf-8") as handle:
                handle.write(LEAVE_LINE + "\n")
            tracker.poll()
            self.assertEqual(tracker.take_events(), [SHIP_LEFT])
            self.assertEqual(tracker.take_events(), [])

    def test_confirmed_location_survives_game_restart(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "Game.log"
            log.write_text(LOCATION_LINE + "\n", encoding="utf-8")
            executable = f"Z:{Path(root).as_posix()}/Bin64/StarCitizen.exe"
            running = [executable]
            tracker = GameLogTracker(executable_finder=lambda: running[0])
            tracker.poll()
            running[0] = None
            tracker.poll()
            self.assertEqual(tracker.facts.location, "Nyx_Levski")
            self.assertEqual(tracker.prompt_context(), "")


if __name__ == "__main__":
    unittest.main()
