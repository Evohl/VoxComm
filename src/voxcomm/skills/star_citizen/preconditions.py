from __future__ import annotations


PRECONDITION_FIELDS = {
    "pilot seat": "pilot_seat",
    "pilotensitz": "pilot_seat",
    "in ship": "in_ship",
    "im schiff": "in_ship",
}
PRECONDITION_VALUES = {"yes": True, "ja": True, "no": False, "nein": False}
REQUIREMENT_TEXT = {
    ("pilot_seat", True): "Captain, ship controls are unavailable until you take the pilot seat.",
    ("pilot_seat", False): "Captain, leave the pilot seat before requesting this action.",
    ("in_ship", True): "Captain, board the ship before requesting this shipboard action.",
    ("in_ship", False): "Captain, leave the ship before requesting this action.",
}
UNKNOWN_STATE_TEXT = {
    "pilot_seat": "Captain, pilot-seat status is unknown. I am waiting for Star Citizen telemetry before enabling ship controls.",
    "in_ship": "Captain, ship status is unknown. I am waiting for Star Citizen telemetry before processing this shipboard action.",
}


def parse_precondition(words: list[str], line_number: int) -> tuple[str, bool] | None:
    field = PRECONDITION_FIELDS.get(" ".join(words[:-1]))
    value = PRECONDITION_VALUES.get(words[-1]) if words else None
    if field is None or value is None:
        raise ValueError(f"line {line_number}: precondition must be [pilot seat yes], [pilot seat no], [in ship yes] or []")
    return field, value