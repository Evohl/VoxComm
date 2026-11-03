from __future__ import annotations

from voxcomm.config import reply_override_key
from voxcomm.prompts import VOICE_LANGUAGE_NAMES
from voxcomm.skills.star_citizen.announcement_translations import SHIP_FALLBACK_NAMES, TRANSLATED_ANNOUNCEMENTS, UNKNOWN_QUANTUM_REPLIES
from voxcomm.skills.star_citizen.gamelog.facts import (
    NAV_TARGET_SET, PILOT_LEFT, PILOT_TAKEN, QUANTUM_ARRIVED, QUANTUM_SIGNATURE_UNKNOWN, SHIP_ENTERED, SHIP_LEFT,
)


GAME_EVENT_TEXT = {
    SHIP_ENTERED: "Ship entered",
    SHIP_LEFT: "Ship left",
    PILOT_TAKEN: "Pilot seat taken",
    PILOT_LEFT: "Pilot seat left",
    NAV_TARGET_SET: "Nav target selected",
    QUANTUM_ARRIVED: "Quantum arrived",
    QUANTUM_SIGNATURE_UNKNOWN: "Unknown quantum signature",
}
GAME_ANNOUNCE_FIELDS = {
    SHIP_ENTERED: "game_announce_ship_entered",
    SHIP_LEFT: "game_announce_ship_left",
    PILOT_TAKEN: "game_announce_pilot_taken",
    PILOT_LEFT: "game_announce_pilot_left",
    NAV_TARGET_SET: "game_announce_nav_target_set",
    QUANTUM_ARRIVED: "game_announce_quantum_arrived",
    QUANTUM_SIGNATURE_UNKNOWN: "game_announce_quantum_signature_unknown",
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
        "DE": "Mit dem Schiffscomputer von {ship} verbunden.",
        "EN": "Connected to {ship}'s ship computer.",
        "FR": "Connecté à l'ordinateur de bord de {ship}.",
    },
    PILOT_LEFT: {
        "DE": "Verbindung zu {ship} getrennt.",
        "EN": "Disconnected from {ship}.",
        "FR": "Déconnecté de {ship}.",
    },
    NAV_TARGET_SET: {"DE": "Captain, Navziel ausgewählt.", "EN": "Captain, nav target selected.",
                     "FR": "Capitaine, destination sélectionnée."},
    QUANTUM_ARRIVED: {"DE": "Captain, Quantum-Ankunft bestätigt.", "EN": "Captain, quantum arrival confirmed.",
                      "FR": "Capitaine, arrivée quantique confirmée."},
    QUANTUM_SIGNATURE_UNKNOWN: {"DE": "Unbekannte Quantum-Signatur entdeckt: {ship}.",
                                "EN": "Unknown quantum signature detected: {ship}.",
                                "FR": "Signature quantique inconnue détectée : {ship}."},
}


def game_reply_defaults(event: str) -> dict[str, str]:
    defaults = GAME_EVENT_REPLIES[event]
    codes = ("en", "de", "fr", *(code for code in VOICE_LANGUAGE_NAMES if code not in {"en", "de", "fr"}))
    return {code.upper(): defaults.get(code.upper()) or (UNKNOWN_QUANTUM_REPLIES[code] if event == QUANTUM_SIGNATURE_UNKNOWN
                                                         else TRANSLATED_ANNOUNCEMENTS[code][event])
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
            PILOT_LEFT: {"DE": "deinem Schiff", "EN": "your ship"},
            QUANTUM_SIGNATURE_UNKNOWN: {"DE": "unbekannter Schiffstyp", "EN": "unknown ship type", "FR": "type de vaisseau inconnu"},
        }
        ship_name = ship.strip() or fallback_names.get(event, {}).get(
            tag, {"DE": "deinem Schiff", "FR": "votre vaisseau"}.get(
                tag, SHIP_FALLBACK_NAMES.get(tag.lower(), "your ship")))
        reply = reply.replace("{ship}", ship_name)
    return reply