import time
from unittest.mock import patch

import pytest

from voxcomm.skills.star_citizen import database
from voxcomm.skills.star_citizen.uex import MAX_RESULTS, query_uex


@pytest.fixture
def snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    def write(resources, *, now=None):
        database.replace_snapshot(resources, now=now)

    return write


@pytest.mark.parametrize("operation", ["commodities", "terminals", "prices", "routes", "vehicles", "vehicle_prices", "items", "item_prices", "item_attributes"])
def test_missing_snapshot_is_offline_and_reports_freshness(snapshot, operation):
    with patch("voxcomm.skills.star_citizen.uex._request_data", side_effect=AssertionError("network")), \
         patch("httpx.get", side_effect=AssertionError("network")):
        result = query_uex(operation, "Gold")
    assert result["status"] == "unavailable"
    assert result["matches"] == []
    assert result["snapshot_status"] == "missing"
    assert result["snapshot_updated_at"] is None
    assert "restart" in result["error"]
    assert "untrusted" in result["warning"]


@pytest.mark.parametrize(("age", "status"), [(10, "fresh"), (database.REFRESH_INTERVAL + 1, "stale"), (-10, "stale")])
def test_snapshot_timestamp_and_freshness(snapshot, age, status):
    snapshot({"commodities": []}, now=time.time() - age)
    result = query_uex("commodities", "Gold")
    assert result["snapshot_status"] == status
    assert result["snapshot_updated_at"].endswith("Z")
    assert "stale" in result["warning"]


def test_exact_name_precedes_limit_and_partial_choices_are_bounded(snapshot):
    commodities = [{"id": index, "name": f"Gold variant {index}"} for index in range(MAX_RESULTS + 3)]
    commodities.append({"id": 99, "name": "Gold"})
    snapshot({"commodities": commodities, "commodities_prices_all": [
        {"id_commodity": 99, "price_sell": 5},
    ]})
    catalog = query_uex("commodities", "gold")
    assert catalog["matches"][0]["id"] == 99
    assert len(catalog["matches"]) == MAX_RESULTS
    assert query_uex("prices", "gold")["matches"][0]["id_commodity"] == 99
    ambiguous = query_uex("prices", "variant")
    assert ambiguous["status"] == "choose_commodity"
    assert len(ambiguous["matches"]) == MAX_RESULTS
    assert ambiguous["snapshot_updated_at"].endswith("Z")


def test_duplicate_exact_commodity_names_require_a_choice(snapshot):
    snapshot({"commodities": [{"id": 1, "name": "Gold"}, {"id": 2, "name": "Gold"}]})
    result = query_uex("routes", "gold")
    assert result["status"] == "choose_commodity"
    assert [row["id"] for row in result["matches"]] == [1, 2]
    assert "not a verified route or profit" in result["route_warning"]


def test_prices_join_terminal_metadata_and_filter_location(snapshot):
    snapshot({
        "commodities": [{"id": 1, "name": "Gold"}],
        "terminals": [{"id": 7, "name": "Trade Desk", "nickname": "Levski Desk", "star_system_name": "Nyx", "planet_name": "Delamar", "game_version": "4.4"}],
        "commodities_prices_all": [
            {"id_commodity": 1, "id_terminal": 7, "price_sell": 100, "planet_name": None, "game_version": "4.3"},
            {"id_commodity": 1, "id_terminal": 8, "price_sell": 200},
        ],
    })
    result = query_uex("prices", "Gold", "Nyx")
    assert len(result["matches"]) == 1
    row = result["matches"][0]
    assert (row["terminal_name"], row["planet_name"], row["star_system_name"]) == ("Trade Desk", "Delamar", "Nyx")
    assert (row["game_version"], row["terminal_game_version"]) == ("4.3", "4.4")
    assert query_uex("prices", "Gold", "Levski Desk")["matches"][0]["id_terminal"] == 7
    unknown = query_uex("prices", "Gold")["matches"][1]
    assert "terminal_name" not in unknown
    assert "planet_name" not in unknown


def test_routes_compare_player_purchase_to_player_sale_without_claiming_availability(snapshot):
    snapshot({
        "commodities": [{"id": 1, "name": "Gold"}],
        "terminals": [
            {"id": 7, "name": "Origin", "star_system_name": "Nyx", "game_version": "4.4"},
            {"id": 8, "name": "Destination", "star_system_name": "Stanton"},
        ],
        "commodities_prices_all": [
            {"id_commodity": 1, "id_terminal": 7, "price_buy": 100, "price_sell": 0},
            {"id_commodity": 1, "id_terminal": 8, "price_buy": 0, "price_sell": 150},
            {"id_commodity": 1, "id_terminal": 0, "price_buy": 1, "price_sell": 1000},
        ],
    })
    result = query_uex("routes", "Gold", "Nyx")
    assert "not a verified route or profit" in result["route_warning"]
    assert len(result["matches"]) == 1
    row = result["matches"][0]
    assert (row["terminal_origin_name"], row["terminal_destination_name"]) == ("Origin", "Destination")
    assert (row["price_origin"], row["price_destination"], row["gross_price_spread"]) == (100, 150, 50)
    assert row["origin_star_system_name"] == "Nyx"
    assert row["origin_terminal_game_version"] == "4.4"
    assert "origin_game_version" not in row
    assert not {"distance", "stock", "availability"} & row.keys()
    assert query_uex("routes", "Gold", "Pyro")["status"] == "no_matches"


def test_vehicle_prices_distinguish_money_purchase_and_rental(snapshot):
    snapshot({
        "vehicles": [{"id": 42, "name": "Hawk"}],
        "terminals": [{"id": 7, "name": "Astro Armada", "city_name": "Area18"}],
        "vehicles_prices": [{"id_vehicle": 42, "price": 120, "currency": "GBP"}],
        "vehicles_purchases_prices_all": [{"id_vehicle": 42, "id_terminal": 7, "price_buy": 2646000}],
        "vehicles_rentals_prices_all": [{"id_vehicle": 42, "id_terminal": 7, "price_rent": 37485}],
    })
    rows = query_uex("vehicle_prices", "Hawk")["matches"]
    assert [row["price_type"] for row in rows] == ["real_money", "in_game_purchase", "in_game_rental"]
    assert rows[0]["currency"] == "GBP"
    assert rows[1]["price_buy"] == 2646000 and rows[1]["city_name"] == "Area18"
    assert rows[2]["price_rent"] == 37485 and "rental_period" not in rows[2]
    assert {row["price_type"] for row in query_uex("vehicle_prices", "Hawk", "Area18")["matches"]} == {"in_game_purchase", "in_game_rental"}


def test_vehicle_prices_do_not_hide_in_game_prices_behind_many_real_money_prices(snapshot):
    snapshot({
        "vehicles": [{"id": 42, "name": "Hawk"}],
        "vehicles_prices": [{"id_vehicle": 42, "price": index + 1, "currency": "USD"} for index in range(MAX_RESULTS + 4)],
        "vehicles_purchases_prices_all": [{"id_vehicle": 42, "price_buy": 100}],
        "vehicles_rentals_prices_all": [{"id_vehicle": 42, "price_rent": 20}],
    })
    rows = query_uex("vehicle_prices", "Hawk")["matches"]
    assert len(rows) == MAX_RESULTS
    assert {row["price_type"] for row in rows} == {"real_money", "in_game_purchase", "in_game_rental"}


def test_exact_entity_price_precedes_partial_matches(snapshot):
    snapshot({
        "items": [{"id": 1, "name": "Atlas Variant"}, {"id": 2, "name": "Atlas"}],
        "items_prices_all": [{"id_item": 1, "price": 1}, {"id_item": 2, "price": 2}],
    })
    rows = query_uex("item_prices", "Atlas")["matches"]
    assert [row["item_name"] for row in rows] == ["Atlas", "Atlas Variant"]


def test_items_attributes_and_prices_join_only_by_id(snapshot):
    snapshot({
        "items": [{"id": 11, "name": "Atlas Drive"}, {"id": 12, "name": "Other"}],
        "items_attributes": [{"id_item": 11, "name": "Power", "value": "10"}, {"id_item": 12, "name": "Power", "value": "20"}],
        "items_prices_all": [{"id_item": 11, "id_terminal": 7, "price_buy": 1234}, {"id_item": 12, "id_terminal": 7, "price_buy": 9999}],
        "terminals": [{"id": 7, "name": "Shop", "moon_name": "Cellin", "game_version": "4.4"}],
    })
    assert query_uex("items", "Atlas")["matches"][0]["id"] == 11
    assert query_uex("item_attributes", "Atlas")["matches"][0]["value"] == "10"
    prices = query_uex("item_prices", "Atlas", "Cellin")["matches"]
    assert len(prices) == 1 and prices[0]["price_buy"] == 1234
    assert prices[0]["terminal_name"] == "Shop" and prices[0]["terminal_game_version"] == "4.4"
    assert "game_version" not in prices[0]


def test_raw_commodity_prices_are_searchable(snapshot):
    snapshot({
        "commodities": [{"id": 12, "name": "Beryl (Raw)"}],
        "commodities_raw_prices_all": [
            {"id_commodity": 12, "id_terminal": 1, "price_buy": 10},
            {"id_commodity": 12, "id_terminal": 2, "price_sell": 20},
        ],
    })
    assert len(query_uex("prices", "Beryl (Raw)")["matches"]) == 2
    assert query_uex("routes", "Beryl (Raw)")["matches"][0]["gross_price_spread"] == 10


def test_snapshot_result_fields_and_values_are_bounded(snapshot):
    row = {"name": "Hawk", "description": "x" * 300, "nested": {"ignored": True}, **{f"extra_{index}": index for index in range(50)}}
    snapshot({"vehicles": [row] * (MAX_RESULTS + 2)})
    rows = query_uex("vehicles", "Hawk")["matches"]
    assert len(rows) == MAX_RESULTS
    assert all(len(entry) == 32 and "nested" not in entry for entry in rows)


def test_full_vehicle_name_is_searchable_and_specifications_are_retained(snapshot):
    snapshot({"vehicles": [{
        "id": 1, "name": "100i", "name_full": "Origin 100i", "company_name": "Origin Jumpworks",
        "scu": 2, "crew": "1", "mass": 45143, **{f"is_feature_{index}": 1 for index in range(50)},
    }]})
    row = query_uex("vehicles", "Origin 100i")["matches"][0]
    assert (row["name_full"], row["company_name"], row["scu"], row["crew"], row["mass"]) == (
        "Origin 100i", "Origin Jumpworks", 2, "1", 45143,
    )
