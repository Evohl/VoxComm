import unittest

from voxcomm.action_catalog import (
    action_reply,
    apply_reply_overrides,
    contains_keyword,
    parse_action_catalog,
    reply_language_tag,
    resolve_utterance,
    unmet_requirement,
    merge_catalog_reply_overrides,
)

SAMPLE = (
    "{call}: call, hail, rufen, kontaktieren\n"
    "{deploy}: deploy, ausfahren, ausklappen\n"
    "{retract}: retract, einfahren, einklappen\n"
    "Landing Gear Deploy => landing gear|fahrwerk + {deploy}, down => Button26 => {DE} Fahrwerk ausgefahren. | {EN} Landing gear deployed\n"
    "Landing Gear Retract => landing gear|fahrwerk + {retract}, up => Button27 => {DE} Fahrwerk eingefahren. | {EN} Landing gear retracted\n"
    "Hangar Call => hangar + {call} => Button31 => {DE} Verbindung zum Hangar hergestellt. | {EN} Hangar contacted\n"
    "Cargo Call => cargo + {call} => Button32 => {DE} Frachtabfertigung kontaktiert. | {EN} Cargo loading contacted\n"
)


class ActionCatalogParseTests(unittest.TestCase):
    def test_sets_expand_into_keyword_groups_and_configured_button(self):
        actions = parse_action_catalog(SAMPLE)

        hangar = actions[2]
        self.assertEqual(hangar["name"], "Hangar Call")
        self.assertEqual(hangar["button"], "Button31")
        self.assertFalse(hangar["hold"])
        self.assertEqual(hangar["keywords"], (("hangar",), ("call", "hail", "rufen", "kontaktieren")))
        self.assertEqual(hangar["replies"], {"DE": "Verbindung zum Hangar hergestellt.", "EN": "Hangar contacted"})

    def test_hold_suffix_marks_action_as_held(self):
        actions = parse_action_catalog("Fire => fire => Button3 hold => {EN} Fire")

        self.assertEqual(actions[0]["button"], "Button3")
        self.assertTrue(actions[0]["hold"])

    def test_rejects_unknown_button_suffixes(self):
        for field in ("Button3 tap", "Button3 hold now", "Button3 Button4"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_action_catalog(f"Gear => gear => {field} => {{EN}} Gear.")

    def test_rejects_malformed_or_empty_keyword_entries(self):
        for line in (
            "Flight Systems On => flight ready => Button1",
            "Flight Systems On => flight ready + => Button1 => {EN} ready",
            "Flight Systems On => |ready => Button1 => {EN} ready",
        ):
            with self.subTest(line=line), self.assertRaises(ValueError):
                parse_action_catalog(line)

    def test_replies_require_distinct_language_tags(self):
        with self.assertRaisesRegex(ValueError, "reply must be"):
            parse_action_catalog("Gear On => gear => Button3 => Gear extended")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_action_catalog("Gear On => gear => Button3 => {DE} a | {DE} b")

    def test_unknown_set_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown set"):
            parse_action_catalog("Gear On => {nope} => Button3 => {EN} Gear extended")

    def test_actions_must_use_distinct_buttons(self):
        catalog = (
            "Flight Systems On => flight ready => Button1 => {EN} enabled\n"
            "Flight Systems Off => flight systems off => Button1 hold => {EN} disabled"
        )

        with self.assertRaisesRegex(ValueError, "own button"):
            parse_action_catalog(catalog)

    def test_action_names_must_be_unique(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_action_catalog("Power On => power on => Button5 => {EN} on\nPower on => power up => Button6 => {EN} up")

    def test_single_actions_without_on_off_pairs_are_valid(self):
        actions = parse_action_catalog(
            "Self Destruct => self destruct|destroy ship => Button11 => {EN} Self destruct.\n"
            "Set Mining Operator Mode => mining mode|switch to mining => Button1 => {EN} Mining mode."
        )

        self.assertEqual([action["button"] for action in actions], ["Button11", "Button1"])

    def test_comments_and_empty_lines_are_ignored(self):
        actions = parse_action_catalog(
            "# note\n\nPower On => power on => Button9 => {EN} enabled\nPower Off => power off => Button10 => {EN} disabled"
        )

        self.assertEqual(len(actions), 2)


class ReplyOverrideTests(unittest.TestCase):
    def test_existing_overrides_migrate_to_editable_catalog(self):
        catalog = "# Comment\nGear => gear => Button1 => {EN} Original | {DE} Original\n"
        migrated, keys = merge_catalog_reply_overrides(catalog, {
            ("Gear", "EN"): r"Open | close \ now", ("Gear", "ES"): "Abierto", ("Missing", "DE"): "Unused"})
        self.assertEqual(keys, {("Gear", "EN"), ("Gear", "ES")})
        self.assertTrue(migrated.startswith("# Comment\n"))
        self.assertEqual(parse_action_catalog(migrated)[0]["replies"],
                         {"EN": r"Open | close \ now", "DE": "Original", "ES": "Abierto"})

    def test_invalid_catalog_keeps_legacy_overrides_unchanged(self):
        catalog = "broken catalog"
        self.assertEqual(merge_catalog_reply_overrides(catalog, {("A", "DE"): "B"}), (catalog, set()))

    def test_conflicting_aliases_and_unrepresentable_override_are_retained(self):
        catalog = "Gear => gear => Button1 => {EN} Original"
        overrides = {("Gear", "CS"): "First", ("Gear", "CZ"): "Second", ("Gear", "EN"): "A => B"}
        self.assertEqual(merge_catalog_reply_overrides(catalog, overrides), (catalog, set()))

    def test_custom_reply_replaces_only_matching_action_and_language(self):
        actions = parse_action_catalog(
            "Gear On => gear => Button1 => {DE} Fahrwerk an. | {EN} Gear on.\n"
            "Gear Off => gear off => Button2 => {DE} Fahrwerk aus. | {EN} Gear off."
        )

        merged = apply_reply_overrides(actions, {("Gear On", "EN"): "Gear is up."})

        self.assertEqual(merged[0]["replies"], {"DE": "Fahrwerk an.", "EN": "Gear is up."})
        self.assertIs(merged[1], actions[1])
        self.assertEqual(action_reply(merged[0], "English"), "Gear is up.")
        self.assertEqual(action_reply(merged[0], "Deutsch"), "Fahrwerk an.")


class ContainsKeywordTests(unittest.TestCase):
    def test_matches_whole_words_with_inflection(self):
        self.assertTrue(contains_keyword("Assistent, Fahrwerk ausfahren", "assistent"))
        self.assertTrue(contains_keyword("Hey Assistenten bitte", "Assistent"))
        self.assertFalse(contains_keyword("Fahrwerk ausfahren", "assistent"))
        self.assertFalse(contains_keyword("Fahrwerk ausfahren", ""))

    def test_multi_word_keyword_needs_contiguous_words(self):
        self.assertTrue(contains_keyword("Hey computer, Hangar", "hey computer"))
        self.assertFalse(contains_keyword("computer, hey Hangar", "hey computer"))


class ActionCatalogResolveTests(unittest.TestCase):
    def setUp(self):
        self.actions = parse_action_catalog(SAMPLE)

    def test_exact_group_match_selects_single_action(self):
        resolution = resolve_utterance(self.actions, "Fahrwerk ausfahren")

        self.assertEqual(resolution.status, "match")
        self.assertEqual([action["name"] for action in resolution.actions], ["Landing Gear Deploy"])

    def test_english_phrase_matches_through_set_alternatives(self):
        resolution = resolve_utterance(self.actions, "Landing gear down")

        self.assertEqual(resolution.status, "match")
        self.assertEqual(resolution.actions[0]["button"], "Button26")

    def test_several_clauses_resolve_to_several_actions(self):
        resolution = resolve_utterance(self.actions, "Fahrwerk ausfahren und Hangar kontaktieren")

        self.assertEqual(resolution.status, "match")
        self.assertEqual([action["name"] for action in resolution.actions], ["Landing Gear Deploy", "Hangar Call"])

    def test_small_typo_in_long_word_still_matches(self):
        resolution = resolve_utterance(self.actions, "Hangar kontaktierne")

        self.assertEqual(resolution.status, "match")
        self.assertEqual(resolution.actions[0]["name"], "Hangar Call")

    def test_shared_keywords_without_distinguishing_word_are_ambiguous(self):
        resolution = resolve_utterance(self.actions, "cargo hangar call")

        self.assertEqual(resolution.status, "ambiguous")
        self.assertEqual(resolution.actions, ())

    def test_on_and_off_pairs_do_not_collide(self):
        actions = parse_action_catalog(
            "Power On => power on => Button5 => {EN} powered on\n"
            "Power Off => power off => Button6 => {EN} powered off"
        )

        resolution = resolve_utterance(actions, "power off")

        self.assertEqual(resolution.status, "match")
        self.assertEqual([action["name"] for action in resolution.actions], ["Power Off"])

    def test_questions_are_not_executed(self):
        self.assertEqual(resolve_utterance(self.actions, "Kannst du das Fahrwerk ausfahren?").status, "question")
        self.assertEqual(resolve_utterance(self.actions, "Wie fahre ich das Fahrwerk aus").status, "question")

    def test_negated_actions_are_never_resolved(self):
        for prompt in ("Fahrwerk nicht ausfahren", "don't deploy landing gear",
                       "do not deploy landing gear", "Fahrwerk ausfahren und Hangar nicht kontaktieren"):
            with self.subTest(prompt=prompt):
                result = resolve_utterance(self.actions, prompt)
                self.assertEqual(result.status, "negated")
                self.assertEqual(result.actions, ())

    def test_unrelated_request_has_no_match(self):
        self.assertEqual(resolve_utterance(self.actions, "Perform the requested action").status, "none")

    def test_reply_follows_requested_language_with_fallback(self):
        hangar = self.actions[2]

        self.assertEqual(action_reply(hangar, "German"), "Verbindung zum Hangar hergestellt.")
        self.assertEqual(action_reply(hangar, "English"), "Hangar contacted")
        self.assertEqual(action_reply(hangar, "French"), "Verbindung zum Hangar hergestellt.")

    def test_reply_language_tags(self):
        self.assertEqual(reply_language_tag("German"), "DE")
        self.assertEqual(reply_language_tag("english"), "EN")
        self.assertEqual(reply_language_tag("Spanish"), "ES")
        self.assertEqual(reply_language_tag("French"), "FR")
        self.assertEqual(reply_language_tag("Czech"), "CS")
        self.assertEqual(reply_language_tag("cz"), "CS")

    def test_french_reply_is_selected_from_catalog(self):
        action = parse_action_catalog("Lights On => lights => Button1 => {EN} Lights on | {FR} Lumières allumées")[0]
        self.assertEqual(action_reply(action, "French"), "Lumières allumées")

    def test_all_configured_languages_and_czech_alias(self):
        from voxcomm.prompts import VOICE_LANGUAGE_NAMES

        for code, language in VOICE_LANGUAGE_NAMES.items():
            with self.subTest(language=language):
                action = parse_action_catalog(f"Action => action => Button1 => {{{code.upper()}}} answer")[0]
                self.assertEqual(reply_language_tag(language), code.upper())
                self.assertEqual(action["replies"][code.upper()], "answer")
        action = parse_action_catalog("Action => action => Button1 => {CZ} Zapnuto")[0]
        self.assertEqual(action_reply(action, "Czech"), "Zapnuto")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_action_catalog("Action => action => Button1 => {CZ} A | {CS} B")


PRECONDITION_CATALOG = (
    "Flight Systems On => flight ready => Button1 => {EN} enabled\n"
    "[pilot seat yes]\n"
    "Quantum Operator Mode => operator mode + quantum => Button5 hold => {EN} Quantum mode selected.\n"
    "Missile Operator Mode => operator mode + missile => Button6 => {EN} Missile mode selected.\n"
    "[]\n"
    "Headlights On => headlights + on => Button64 => {EN} Headlights on.\n"
)


class ActionPreconditionTests(unittest.TestCase):
    def test_directive_applies_to_following_rows_until_cleared(self):
        actions = {action["name"]: action for action in parse_action_catalog(PRECONDITION_CATALOG)}

        self.assertIsNone(actions["Flight Systems On"]["requires"])
        self.assertEqual(actions["Quantum Operator Mode"]["requires"], ("pilot_seat", True))
        self.assertEqual(actions["Missile Operator Mode"]["requires"], ("pilot_seat", True))
        self.assertIsNone(actions["Headlights On"]["requires"])

    def test_german_and_no_values_are_parsed(self):
        actions = parse_action_catalog(
            "[pilotensitz nein]\nFire => fire => Button3 => {EN} Fire\n"
            "[in ship ja]\nScan => scan => Button4 => {EN} Scan\n")

        self.assertEqual(actions[0]["requires"], ("pilot_seat", False))
        self.assertEqual(actions[1]["requires"], ("in_ship", True))

    def test_rejects_malformed_directives(self):
        for line in ("[pilot seat maybe]", "[unknown yes]", "[pilot seat yes", "[yes]"):
            with self.subTest(line=line), self.assertRaises(ValueError):
                parse_action_catalog(f"{line}\nFire => fire => Button3 => {{EN}} Fire")

    def test_unmet_requirement_blocks_unknown_and_contradictory_state(self):
        actions = {action["name"]: action for action in parse_action_catalog(PRECONDITION_CATALOG)}
        quantum = actions["Quantum Operator Mode"]

        self.assertEqual(unmet_requirement(quantum, {"pilot_seat": False}), ("pilot_seat", True))
        self.assertIsNone(unmet_requirement(quantum, {"pilot_seat": True}))
        self.assertEqual(unmet_requirement(quantum, {"pilot_seat": None}), ("pilot_seat", True))
        self.assertEqual(unmet_requirement(quantum, {}), ("pilot_seat", True))
        self.assertIsNone(unmet_requirement(actions["Headlights On"], {"pilot_seat": False}))

        scan = parse_action_catalog("[in ship yes]\nScan => scan => Button41 => {EN} Scanning")[0]
        self.assertEqual(unmet_requirement(scan, {"in_ship": None}), ("in_ship", True))

    def test_resolution_still_finds_blocked_action(self):
        actions = parse_action_catalog(PRECONDITION_CATALOG)

        resolution = resolve_utterance(actions, "operator mode quantum")

        self.assertEqual(resolution.status, "match")
        self.assertEqual(resolution.actions[0]["name"], "Quantum Operator Mode")


if __name__ == "__main__":
    unittest.main()
