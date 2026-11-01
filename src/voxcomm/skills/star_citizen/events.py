from __future__ import annotations

from voxcomm.config import reply_override_key
from voxcomm.prompts import VOICE_LANGUAGE_NAMES
from voxcomm.skills.star_citizen.announcement_translations import SHIP_FALLBACK_NAMES, TRANSLATED_ANNOUNCEMENTS
from voxcomm.skills.star_citizen.gamelog.facts import (
    NAV_TARGET_SET, PILOT_LEFT, PILOT_TAKEN, QUANTUM_ARRIVED, SHIP_ENTERED, SHIP_LEFT,
)


GAME_EVENT_TEXT = {
    SHIP_ENTERED: "Ship entered",
    SHIP_LEFT: "Ship left",
    PILOT_TAKEN: "Pilot seat taken",
    PILOT_LEFT: "Pilot seat left",
    NAV_TARGET_SET: "Nav target selected",
    QUANTUM_ARRIVED: "Quantum arrived",
}
GAME_ANNOUNCE_FIELDS = {
    SHIP_ENTERED: "game_announce_ship_entered",
    SHIP_LEFT: "game_announce_ship_left",
    PILOT_TAKEN: "game_announce_pilot_taken",
    PILOT_LEFT: "game_announce_pilot_left",
    NAV_TARGET_SET: "game_announce_nav_target_set",
    QUANTUM_ARRIVED: "game_announce_quantum_arrived",
}
GAME_EVENT_REPLIES = {
    SHIP_ENTERED: {
        "DE": "Captain, {ship} über Mobiglas erkannt.",
        "EN": "{ship} detected via Mobiglas, Captain.",
        "FR": "Capitaine, {ship} détecté via le Mobiglas.",
    },
    SHIP_LEFT: {
        "DE": "Captain, Verbindung zu {ship} beendet. Mobiglas bleibt aktiv.",
        "EN": "Connection to {ship} ended. Mobiglas remains active, Captain.",
        "FR": "Connexion à {ship} terminée. Le Mobiglas reste actif, capitaine.",
    },
    PILOT_TAKEN: {
        "DE": "Verbindung zu {ship} erfolgreich, Captain.",
        "EN": "Connection to {ship} successful, Captain.",
        "FR": "Connexion à {ship} établie, capitaine.",
    },
    PILOT_LEFT: {
        "DE": "Schiffssteuerung getrennt; Mobiglas bleibt aktiv, Captain.",
        "EN": "Ship controls disconnected; Mobiglas remains active, Captain.",
        "FR": "Commandes du vaisseau déconnectées ; le Mobiglas reste actif, capitaine.",
    },
    NAV_TARGET_SET: {"DE": "Captain, Navziel ausgewählt.", "EN": "Captain, nav target selected.",
                     "FR": "Capitaine, destination sélectionnée."},
    QUANTUM_ARRIVED: {"DE": "Captain, Quantum-Ankunft bestätigt.", "EN": "Captain, quantum arrival confirmed.",
                      "FR": "Capitaine, arrivée quantique confirmée."},
}


def game_reply_defaults(event: str) -> dict[str, str]:
    defaults = GAME_EVENT_REPLIES[event]
    codes = ("en", "de", "fr", *(code for code in VOICE_LANGUAGE_NAMES if code not in {"en", "de", "fr"}))
    return {code.upper(): defaults.get(code.upper()) or TRANSLATED_ANNOUNCEMENTS[code][event]
            for code in codes}


def game_reply_action(event: str) -> str:
    return f"game_log:{event}"


def game_event_reply(overrides: dict[str, str], event: str, profile: str, tag: str, ship: str = "") -> str:
    reply = (overrides.get(reply_override_key(profile, game_reply_action(event), tag))
             or game_reply_defaults(event).get(tag) or GAME_EVENT_REPLIES[event]["EN"])
    if "{ship}" in reply:
        fallback_names = {
            SHIP_ENTERED: {"DE": "Schiff", "EN": "Ship"},
            SHIP_LEFT: {"DE": "deinem Schiff", "EN": "your ship"},
            PILOT_TAKEN: {"DE": "deinem Schiff", "EN": "your ship"},
        }
        ship_name = ship.strip() or fallback_names.get(event, {}).get(
            tag, {"DE": "deinem Schiff", "FR": "votre vaisseau"}.get(
                tag, SHIP_FALLBACK_NAMES.get(tag.lower(), "your ship")))
        reply = reply.replace("{ship}", ship_name)
    return reply