from unittest.mock import Mock

import pytest

from voxcomm.skills.star_citizen.uex import MAX_RESULTS, query_uex


def response(rows):
    return Mock(content=b"{}", json=Mock(return_value={"status": "ok", "data": rows}))


@pytest.mark.parametrize("operation", ["commodities", "terminals", "prices", "routes"])
def test_result_fields_are_bounded_semantic_and_independent_of_input_order(operation):
    semantic = {
        "id": 71,
        "id_commodity": 17,
        "name": "Agricium at Levski",
        "commodity_name": "Agricium",
        "terminal_name": "Levski",
        "terminal_origin_name": "Levski",
        "terminal_destination_name": "Area 18",
        "star_system_name": "Nyx",
        "planet_name": "Delamar",
        "moon_name": "A moon",
        "game_version": "4.4",
        "price_buy": 123,
        "price_sell": 456,
        "date_modified": 1760000000,
        "scu_buy": 999,
        "status_sell": "available",
    }
    row = {**{f"extra_{index:02}": index for index in range(45)}, **semantic,
           "nested": {"ignored": True}, "long_text": "x" * 300}
    commodity = {"id": 17, "name": "Agricium"}

    def run(data):
        responses = [response([data])]
        if operation in ("prices", "routes"):
            responses.insert(0, response([commodity]))
        get = Mock(side_effect=responses)
        result = query_uex(operation, "Agricium", get=get)
        assert get.call_count == len(responses)
        return result["matches"][0]

    selected = run(row)
    reversed_selected = run(dict(reversed(list(row.items()))))
    assert selected == reversed_selected
    assert list(selected) == list(reversed_selected)
    assert all(selected[key] == value for key, value in semantic.items())
    assert len(selected) == 32
    assert "nested" not in selected
    assert [key for key in selected if key.startswith("extra_")] == [
        f"extra_{index:02}" for index in range(16)
    ]


def test_core_fields_outweigh_many_other_semantic_fields():
    semantic = {
        "id": 71, "commodity_name": "Gold", "terminal_origin_name": "Levski",
        "terminal_destination_name": "Area 18", "star_system_name": "Nyx",
        "planet_name": "Delamar", "moon_name": "Moon", "game_version": "4.4",
        "price_buy": 123, "price_sell": 456, "date_modified": 1760000000,
        "scu_buy": 999, "status_buy": "available", "status_sell": "low",
    }
    row = {**{f"price_other_{index:02}": index for index in range(50)}, **semantic}
    get = Mock(side_effect=[response([{"id": 17, "name": "Gold"}]), response([row])])

    selected = query_uex("routes", "Gold", get=get)["matches"][0]

    assert all(selected[key] == value for key, value in semantic.items())
    assert len(selected) == 32


@pytest.mark.parametrize("operation", ["prices", "routes"])
def test_exact_commodity_after_result_limit_is_resolved_before_request(operation):
    commodities = [{"id": index, "name": f"Gold variant {index}"} for index in range(MAX_RESULTS + 3)]
    commodities.append({"id": 99, "name": "Gold"})
    get = Mock(side_effect=[response(commodities), response([{"commodity_name": "Gold"}])])

    result = query_uex(operation, "gold", get=get)

    assert result["status"] == "ok"
    assert get.call_count == 2
    assert get.call_args.kwargs["params"] == {"id_commodity": "99"}


@pytest.mark.parametrize("operation", ["prices", "routes"])
def test_ambiguous_commodities_remain_bounded_without_an_extra_request(operation):
    commodities = [{"id": index, "name": f"Gold variant {index}"} for index in range(MAX_RESULTS + 3)]
    get = Mock(return_value=response(commodities))

    result = query_uex(operation, "Gold", get=get)

    assert result["status"] == "choose_commodity"
    assert len(result["matches"]) == MAX_RESULTS
    get.assert_called_once()


def test_scalar_and_string_bounds_are_preserved():
    row = {"name": "Levski", "description": "x" * 300, "nested": ["not scalar"],
           "n" * 100: "value", "nullable": None, "enabled": False}
    get = Mock(return_value=response([row]))

    selected = query_uex("terminals", "Levski", get=get)["matches"][0]

    assert selected["description"] == "x" * 240
    assert selected["n" * 80] == "value"
    assert selected["nullable"] is None
    assert selected["enabled"] is False
    assert "nested" not in selected