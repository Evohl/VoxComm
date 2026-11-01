import sys

from voxcomm.skills.star_citizen.gamelog import (
	PILOT_LEFT, PILOT_TAKEN, SHIP_ENTERED, SHIP_LEFT, GameFacts, GameLogTracker,
	facts, paths, process, reader,
)

sys.modules[f"{__name__}.facts"] = facts
sys.modules[f"{__name__}.paths"] = paths
sys.modules[f"{__name__}.process"] = process
sys.modules[f"{__name__}.reader"] = reader

__all__ = ["GameFacts", "GameLogTracker", "PILOT_LEFT", "PILOT_TAKEN", "SHIP_ENTERED", "SHIP_LEFT"]
