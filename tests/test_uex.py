import unittest
from unittest.mock import Mock

from voxcomm.providers.uex import MAX_RESULTS, query_uex


class UexQueryTests(unittest.TestCase):
    def test_terminal_search_uses_public_api_and_returns_limited_community_data(self):
        response = Mock(
            content=b"{}",
            json=Mock(return_value={
                "status": "ok",
                "data": [
                    {"id": index, "name": "Area 18", "city_name": "Area 18"}
                    for index in range(MAX_RESULTS + 2)
                ],
            }),
        )
        response.raise_for_status.return_value = None
        get = Mock(return_value=response)

        result = query_uex("terminals", "area 18", get=get)

        get.assert_called_once()
        self.assertIn("/terminals/", get.call_args.args[0])
        self.assertEqual(get.call_args.kwargs["params"], {"type": "commodity"})
        self.assertEqual(len(result["matches"]), MAX_RESULTS)
        self.assertIn("may be stale", result["warning"])

    def test_price_query_resolves_commodity_id_before_requesting_prices(self):
        commodity_response = Mock(
            content=b"{}",
            json=Mock(return_value={"status": "ok", "data": [{"id": 17, "name": "Agricium"}]}),
        )
        price_response = Mock(
            content=b"{}",
            json=Mock(return_value={"status": "ok", "data": [{"commodity_name": "Agricium", "price_sell": 123}]}),
        )
        get = Mock(side_effect=[commodity_response, price_response])

        result = query_uex("prices", "Agricium", get=get)

        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args.kwargs["params"], {"id_commodity": "17"})
        self.assertEqual(result["matches"][0]["price_sell"], 123)

    def test_route_location_filter_matches_origin_and_destination_terminal_fields(self):
        commodity_response = Mock(
            content=b"{}",
            json=Mock(return_value={"status": "ok", "data": [{"id": 17, "name": "Agricium"}]}),
        )
        routes_response = Mock(
            content=b"{}",
            json=Mock(return_value={"status": "ok", "data": [
                {"commodity_name": "Agricium", "origin_terminal_name": "Levski",
                 "destination_terminal_name": "Area 18"},
                {"commodity_name": "Agricium", "origin_terminal_name": "Levski",
                 "destination_terminal_name": "Lorville"},
            ]}),
        )
        get = Mock(side_effect=[commodity_response, routes_response])

        result = query_uex("routes", "Agricium", "Area 18", get=get)

        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["destination_terminal_name"], "Area 18")

    def test_ambiguous_commodity_returns_candidates_without_querying_prices(self):
        response = Mock(
            content=b"{}",
            json=Mock(return_value={"status": "ok", "data": [
                {"id": 1, "name": "Agricium"},
                {"id": 2, "name": "Agricium Ore"},
            ]}),
        )
        get = Mock(return_value=response)

        result = query_uex("prices", "Agric", get=get)

        self.assertEqual(result["status"], "choose_commodity")
        get.assert_called_once()

    def test_invalid_operation_and_empty_query_are_rejected(self):
        get = Mock()

        with self.assertRaises(ValueError):
            query_uex("anything", "item", get=get)
        with self.assertRaises(ValueError):
            query_uex("commodities", "  ", get=get)
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()