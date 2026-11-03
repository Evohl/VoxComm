from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from unittest.mock import Mock

from voxcomm.action_catalog import parse_action_catalog
from voxcomm.skills.controller.tool import _precondition_message
from voxcomm.skills.star_citizen.gamelog import facts as facts_module
from voxcomm.skills.star_citizen.gamelog.facts import (
    GameFacts,
    GameLogTracker,
    PILOT_LEFT,
    PILOT_TAKEN,
    SHIP_ENTERED,
    SHIP_LEFT,
)


JOIN = "You have joined channel 'Ship : Player'"
LEAVE = "You have left the channel 'Ship : Player'"
EXIT = "ClearDriver: Local client node [99] releasing control token for 'CRUS_Spirit_C1_123456' [123456]"
PILOT = "Failed to get starmap route data! | CRUS_Spirit_C1_123456[123456]|CSCItemNavigation::GetRoute|No Route loaded"


def test_leaving_ship_revokes_pilot_only_control():
    facts = GameFacts()
    assert facts.apply([JOIN, EXIT, PILOT]) == [SHIP_ENTERED, PILOT_LEFT, PILOT_TAKEN]
    actions = tuple(parse_action_catalog(
        "[pilot seat yes]\n"
        "Boost On => boost on => Button2 => {EN} Boost on. | {DE} Schub an."
    ))
    assert not _precondition_message(actions, {"pilot_seat": facts.pilot_seat})

    assert facts.apply([LEAVE]) == [SHIP_LEFT, PILOT_LEFT]
    assert facts.apply([LEAVE]) == []
    assert facts.pilot_seat is False
    assert _precondition_message(actions, {"pilot_seat": facts.pilot_seat})


def test_concurrent_polls_serialize_reader_and_preserve_cursor(monkeypatch, tmp_path):
    first_read_started = Event()
    release_first_read = Event()
    second_poll_started = Event()
    overlapping_read = Event()
    reader_lock = Lock()
    active_reads = [0]
    cursor = [0]
    lines = [[JOIN, EXIT], [PILOT]]

    def read_new_lines():
        with reader_lock:
            active_reads[0] += 1
            if active_reads[0] > 1:
                overlapping_read.set()
        try:
            position = cursor[0]
            if position == 0:
                first_read_started.set()
                assert release_first_read.wait(5)
            cursor[0] = position + 1
            return lines[position] if position < len(lines) else []
        finally:
            with reader_lock:
                active_reads[0] -= 1

    reader = Mock(read_new_lines=Mock(side_effect=read_new_lines))
    monkeypatch.setattr(facts_module, "LogReader", lambda path: reader)
    monkeypatch.setattr(facts_module, "log_path_from_executable", lambda executable: tmp_path / "Game.log")
    tracker = GameLogTracker(executable_finder=lambda: "game.exe")

    def second_poll():
        second_poll_started.set()
        return tracker.prompt_context()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(tracker.poll)
        try:
            assert first_read_started.wait(5)
            second = executor.submit(second_poll)
            assert second_poll_started.wait(5)
            assert not overlapping_read.wait(0.2)
            assert not second.done()
        finally:
            release_first_read.set()
        first.result(timeout=5)
        context = second.result(timeout=5)

    assert cursor[0] == 2
    assert reader.read_new_lines.call_count == 2
    assert (tracker.facts.in_ship, tracker.facts.pilot_seat) == (True, True)
    assert "Im Schiff: ja" in context
    assert "Pilotensitz: ja" in context
    assert tracker.take_events() == [PILOT_TAKEN]


def test_snapshots_and_events_wait_for_complete_poll(monkeypatch, tmp_path):
    applying = Event()
    release_apply = Event()
    snapshot_started = Event()
    events_started = Event()
    snapshot_done = Event()
    events_done = Event()
    original_apply = GameFacts.apply

    def paused_apply(self, lines):
        if lines == [JOIN, EXIT, PILOT]:
            events = original_apply(self, [JOIN, EXIT])
            applying.set()
            assert release_apply.wait(5)
            return events + original_apply(self, [PILOT])
        return original_apply(self, lines)

    monkeypatch.setattr(GameFacts, "apply", paused_apply)
    monkeypatch.setattr(facts_module, "LogReader", lambda path: Mock(
        read_new_lines=Mock(side_effect=[[], [JOIN, EXIT, PILOT]])
    ))
    monkeypatch.setattr(facts_module, "log_path_from_executable", lambda executable: tmp_path / "Game.log")
    tracker = GameLogTracker(executable_finder=lambda: "game.exe")
    tracker.poll()
    old_snapshot = tracker.facts

    def snapshot():
        snapshot_started.set()
        result = tracker.facts
        snapshot_done.set()
        return result

    def take_events():
        events_started.set()
        result = tracker.take_events()
        events_done.set()
        return result

    with ThreadPoolExecutor(max_workers=3) as executor:
        poll = executor.submit(tracker.poll)
        try:
            assert applying.wait(5)
            snapshot_future = executor.submit(snapshot)
            events_future = executor.submit(take_events)
            assert snapshot_started.wait(5)
            assert events_started.wait(5)
            assert not snapshot_done.wait(0.1)
            assert not events_done.wait(0.1)
        finally:
            release_apply.set()
        poll.result(timeout=5)
        new_snapshot = snapshot_future.result(timeout=5)
        events = events_future.result(timeout=5)

    assert (old_snapshot.in_ship, old_snapshot.pilot_seat) == (None, None)
    assert (new_snapshot.in_ship, new_snapshot.pilot_seat) == (True, True)
    assert events == [SHIP_ENTERED, PILOT_LEFT, PILOT_TAKEN]
    new_snapshot.pilot_seat = False
    assert tracker.facts.pilot_seat is True