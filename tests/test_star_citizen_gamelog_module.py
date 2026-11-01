import importlib
import pickle
import subprocess
import sys
from unittest.mock import Mock, patch

import pytest


LEGACY = "voxcomm.gamelog"
SKILL = "voxcomm.skills.star_citizen.gamelog"
MODULES = ("facts", "paths", "process", "reader")


@pytest.mark.parametrize("first,second", [(LEGACY, SKILL), (SKILL, LEGACY)])
def test_import_orders_share_modules_and_objects(first, second):
    code = """
import importlib
import sys

first, second = sys.argv[1:]
importlib.import_module(first)
importlib.import_module(second)
for name in ('facts', 'paths', 'process', 'reader'):
    legacy = importlib.import_module('voxcomm.gamelog.' + name)
    skill = importlib.import_module('voxcomm.skills.star_citizen.gamelog.' + name)
    assert legacy is skill
    assert getattr(importlib.import_module('voxcomm.gamelog'), name) is skill
    assert getattr(importlib.import_module('voxcomm.skills.star_citizen.gamelog'), name) is skill
assert importlib.import_module('voxcomm.gamelog').GameLogTracker is importlib.import_module(
    'voxcomm.skills.star_citizen.gamelog').GameLogTracker
assert importlib.import_module('voxcomm.gamelog.facts').GameFacts.__module__ == (
    'voxcomm.skills.star_citizen.gamelog.facts')
"""
    subprocess.run([sys.executable, "-c", code, first, second], check=True)


@pytest.mark.parametrize("name", MODULES)
def test_legacy_and_skill_module_names_are_identical(name):
    legacy = importlib.import_module(f"{LEGACY}.{name}")
    skill = importlib.import_module(f"{SKILL}.{name}")
    assert legacy is skill
    assert sys.modules[f"{LEGACY}.{name}"] is skill


@pytest.mark.parametrize("name", MODULES)
def test_legacy_shim_restores_module_alias(monkeypatch, name):
    skill = importlib.import_module(f"{SKILL}.{name}")
    importlib.import_module(LEGACY)
    monkeypatch.delitem(sys.modules, f"{LEGACY}.{name}")
    assert importlib.import_module(f"{LEGACY}.{name}") is skill


def test_legacy_pickled_class_reference_resolves_to_skill_class():
    facts = importlib.import_module(f"{SKILL}.facts")
    assert pickle.loads(b"cvoxcomm.gamelog.facts\nGameFacts\n.") is facts.GameFacts


@pytest.mark.parametrize("patch_package", [LEGACY, SKILL])
def test_default_finder_remains_bound_at_definition_time(patch_package):
    facts = importlib.import_module(f"{SKILL}.facts")
    default_finder = facts.GameLogTracker.__init__.__defaults__[0]
    with patch(f"{patch_package}.facts.find_game_executable") as finder:
        assert facts.GameLogTracker()._find_executable is default_finder
        assert default_finder is not finder


@pytest.mark.parametrize("patch_package", [LEGACY, SKILL])
def test_tracker_uses_patched_module_dependencies(patch_package, tmp_path):
    facts = importlib.import_module(f"{SKILL}.facts")
    log_path = tmp_path / "Game.log"
    reader = Mock()
    reader.read_new_lines.return_value = []
    with (
        patch(f"{patch_package}.facts.log_path_from_executable", return_value=log_path) as path_for,
        patch(f"{patch_package}.facts.LogReader", return_value=reader) as reader_class,
        patch(f"{patch_package}.facts.find_game_executable", return_value="game.exe") as finder,
    ):
        tracker = facts.GameLogTracker(executable_finder=finder)
        tracker.poll()
    finder.assert_called_once_with()
    path_for.assert_called_once_with("game.exe")
    reader_class.assert_called_once_with(log_path)
    reader.read_new_lines.assert_called_once_with()


@pytest.mark.parametrize("patch_package,other_package", [(LEGACY, SKILL), (SKILL, LEGACY)])
def test_monkeypatch_is_visible_through_both_module_names(monkeypatch, patch_package, other_package):
    patched = importlib.import_module(f"{patch_package}.process")
    other = importlib.import_module(f"{other_package}.process")
    finder = Mock(return_value="game.exe")
    monkeypatch.setattr(patched, "find_game_executable", finder)
    assert other.find_game_executable() == "game.exe"
    finder.assert_called_once_with()