from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock

from .paths import log_path_from_executable
from .process import find_game_executable
from .reader import LogReader

LOCATION_RE = re.compile(r"requested inventory for Location\[(?P<location>[^\]]+)\]")
STREAMING_SOC_RE = re.compile(r"\bStreamingSOC_(?P<region>[A-Za-z0-9_-]+)")
DATABASE_VERSION_RE = re.compile(
    r"\bDatabase version:\s*sc-alpha-(?P<version>\d+(?:\.\d+){1,2})-(?P<build>\d+)",
    re.IGNORECASE,
)
SHIP_JOIN_RE = re.compile(r"You have joined channel '(?P<channel>[^']+?) : [^']+'")
SHIP_LEAVE_RE = re.compile(r"You have left the channel '(?P<channel>[^']+?) : [^']+'")
SHIP_EXIT_RE = re.compile(r"ClearDriver: .*?releasing control token for '(?P<vehicle>[^']+)'")
NO_ROUTE_RE = re.compile(r"Failed to get starmap route data!.*No Route loaded")
QUANTUM_TARGET_RE = re.compile(r"Player Selected Quantum Target.*?has selected point (?P<target>\S+) as their destination")
ROUTE_CALC_RE = re.compile(r"Successfully calculated route to (?P<target>\S+) fuel estimate (?P<fuel>\d+(?:\.\d+)?)")
QUANTUM_ARRIVED_RE = re.compile(r"Quantum Drive has arrived at final destination")
VEHICLE_ID_SUFFIX_RE = re.compile(r"_\d{6,}$")
UNKNOWN = "unbekannt"
INITIAL_VERSION_SCAN_BYTES = 1024 * 1024

SHIP_ENTERED = "ship_entered"
SHIP_LEFT = "ship_left"
PILOT_TAKEN = "pilot_taken"
PILOT_LEFT = "pilot_left"
NAV_TARGET_SET = "nav_target_set"
QUANTUM_ARRIVED = "quantum_arrived"


def _transition_events(before: tuple[bool | None, bool | None], after: tuple[bool | None, bool | None]) -> list[str]:
    (was_in_ship, was_pilot), (in_ship, pilot_seat) = before, after
    events: list[str] = []
    if in_ship is not None and in_ship != was_in_ship:
        events.append(SHIP_ENTERED if in_ship else SHIP_LEFT)
    if pilot_seat is not None and pilot_seat != was_pilot:
        events.append(PILOT_TAKEN if pilot_seat else PILOT_LEFT)
    return events


@dataclass
class GameFacts:
    location: str = ""
    streaming_region: str = ""
    game_version: str = ""
    game_build: str = ""
    ship: str = ""
    in_ship: bool | None = None
    pilot_seat: bool | None = None
    quantum_target: str = ""
    nav_target: str = ""
    quantum_fuel: str = ""

    def apply(self, lines: list[str]) -> list[str]:
        """Apply log lines and return the state transitions they caused."""
        events: list[str] = []
        for line in lines:
            before = (self.in_ship, self.pilot_seat)
            event = self._apply_line(line)
            events.extend(_transition_events(before, (self.in_ship, self.pilot_seat)))
            if event:
                events.append(event)
        return events

    def _apply_line(self, line: str) -> str | None:
        if match := STREAMING_SOC_RE.search(line):
            self.streaming_region = f"StreamingSOC_{match['region']}"
        if match := DATABASE_VERSION_RE.search(line):
            self.game_version = match["version"]
            self.game_build = match["build"]
        if match := LOCATION_RE.search(line):
            self.location = match["location"]
        elif QUANTUM_ARRIVED_RE.search(line):
            self.quantum_target = ""
            self.nav_target = ""
            self.quantum_fuel = ""
            return QUANTUM_ARRIVED
        elif match := SHIP_JOIN_RE.search(line):
            self.ship = match["channel"]
            self.in_ship = True
        elif match := SHIP_LEAVE_RE.search(line):
            self.ship = match["channel"]
            self.in_ship = False
            if self.pilot_seat is not None:
                self.pilot_seat = False
        elif match := SHIP_EXIT_RE.search(line):
            self.ship = self.ship or VEHICLE_ID_SUFFIX_RE.sub("", match["vehicle"])
            self.pilot_seat = False
        elif NO_ROUTE_RE.search(line):
            self.pilot_seat = True
        elif match := QUANTUM_TARGET_RE.search(line):
            self.quantum_target = match["target"]
        elif match := ROUTE_CALC_RE.search(line):
            changed = match["target"] != self.nav_target
            self.nav_target = match["target"]
            self.quantum_fuel = match["fuel"]
            return NAV_TARGET_SET if changed else None

    def describe(self) -> str:
        in_ship = {True: "ja", False: "nein"}.get(self.in_ship, UNKNOWN)
        pilot_seat = {True: "ja", False: "nein"}.get(self.pilot_seat, UNKNOWN)
        return "\n".join([
            "Star Citizen läuft. Live-Status aus dem Game.log (nur diese Fakten verwenden, nichts erfinden):",
            f"Spielversion: {self.game_version or UNKNOWN}",
            f"Game-Build: {self.game_build or UNKNOWN}",
            f"Ungefähre Region (Streaming-SOC): {self.streaming_region or UNKNOWN}",
            f"Letzter bekannter Aufenthalt (Inventory-Location): {self.location or UNKNOWN}",
            f"Schiff: {self.ship or UNKNOWN}",
            f"Im Schiff: {in_ship}",
            f"Pilotensitz: {pilot_seat}",
            f"Quantenziel: {self.quantum_target or UNKNOWN}",
            f"Geplantes Navziel: {self.nav_target or UNKNOWN}",
            f"QT-Treibstoffschätzung: {self.quantum_fuel or UNKNOWN}",
        ])


class GameLogTracker:
    def __init__(self, executable_finder: Callable[[], str | None] = find_game_executable):
        self._lock = RLock()
        self._find_executable = executable_finder
        self._path: Path | None = None
        self._reader: LogReader | None = None
        self._facts = GameFacts()
        self._events: list[str] = []
        self._primed = False

    @property
    def facts(self) -> GameFacts:
        with self._lock:
            return replace(self._facts)

    @property
    def path(self) -> Path | None:
        with self._lock:
            return self._path

    def poll(self) -> None:
        """Read new lines of the running game's log. Transitions are queued for take_events()."""
        with self._lock:
            executable = self._find_executable()
            path = log_path_from_executable(executable) if executable else None
            if path is None:
                self._path = None
                self._reader = None
                self._facts = GameFacts(location=self._facts.location)
                self._primed = False
                return
            if path != self._path:
                self._path = path
                self._reader = LogReader(path)
                self._facts = GameFacts(location=self._facts.location)
                self._load_game_version(path)
                self._primed = False
            events = self._facts.apply(self._reader.read_new_lines())
            # The first read only catches up with existing log content; it is not announced.
            if self._primed:
                self._events.extend(events)
            self._primed = True

    def take_events(self) -> list[str]:
        with self._lock:
            events, self._events = self._events, []
            return events

    def _load_game_version(self, path: Path) -> None:
        try:
            with path.open("rb") as log_file:
                header = log_file.read(INITIAL_VERSION_SCAN_BYTES).decode("utf-8", "replace")
        except OSError:
            return
        for line in header.splitlines():
            if DATABASE_VERSION_RE.search(line):
                self._facts.apply([line])
                return

    def prompt_context(self) -> str:
        with self._lock:
            self.poll()
            if self._path is None:
                return ""
            return self._facts.describe()
