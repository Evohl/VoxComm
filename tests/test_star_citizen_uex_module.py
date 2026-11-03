import json
import subprocess
import sys
from unittest.mock import Mock, patch

import pytest

import voxcomm.providers.uex as legacy_uex
from voxcomm.providers.llm import _execute_tool, query_uex
from voxcomm.skills.star_citizen import database, uex as skill_uex
from voxcomm.skills.star_citizen.tool import UEX_OPERATIONS


@pytest.mark.parametrize("first_module", ["voxcomm.providers.uex", "voxcomm.skills.star_citizen.uex"])
def test_legacy_and_skill_imports_share_one_implementation_in_either_order(first_module):
    code = (
        "import importlib, sys; "
        f"importlib.import_module({first_module!r}); "
        "legacy = importlib.import_module('voxcomm.providers.uex'); "
        "skill = importlib.import_module('voxcomm.skills.star_citizen.uex'); "
        "assert legacy is skill; "
        "assert sys.modules['voxcomm.providers.uex'] is skill; "
        "assert skill.query_uex.__module__ == 'voxcomm.skills.star_citizen.uex'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
    assert legacy_uex is skill_uex
    assert query_uex is skill_uex.query_uex
    assert UEX_OPERATIONS is skill_uex.OPERATIONS


def test_legacy_httpx_patch_is_visible_from_skill_module():
    response = Mock(content=b"{}", json=Mock(return_value={
        "status": "ok", "data": [{"name": "Levski"}],
    }))
    with patch("voxcomm.providers.uex.httpx.get", return_value=response) as get:
        assert skill_uex.httpx.get is get
        result = skill_uex.query_uex("terminals", "Levski", get=skill_uex.httpx.get)
    get.assert_called_once()
    assert result["matches"] == [{"name": "Levski"}]


@pytest.mark.parametrize(
    ("permissions", "allowed"),
    [
        (set(), False),
        ({"star_citizen_log"}, False),
        ({"unknown_skill"}, False),
        ({"star_citizen_uex"}, True),
        ({"star_citizen"}, True),
    ],
)
def test_shared_implementation_respects_dispatch_permissions(permissions, allowed, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    database.replace_snapshot({"terminals": [{"name": "Levski"}]})
    call = {"function": {"name": "star_citizen", "arguments": {
        "operation": "terminals", "query": "Levski",
    }}}
    with patch.object(database, "read_snapshot", wraps=database.read_snapshot) as read, \
         patch("voxcomm.providers.uex._request_data", side_effect=AssertionError("network")) as request:
        result = json.loads(_execute_tool(call, permissions))
    request.assert_not_called()
    if allowed:
        read.assert_called_once_with()
        assert result["matches"] == [{"name": "Levski"}]
    else:
        read.assert_not_called()
        assert result == {"error": "Tool is not enabled"}