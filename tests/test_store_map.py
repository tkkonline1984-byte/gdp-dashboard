import unittest

from store_map import (
    current_product_locations,
    is_complete_location,
    location_label,
    locations_differ,
    normalize_location,
    parse_store_zones,
    store_map_svg,
)


class StoreMapTests(unittest.TestCase):
    def test_normalizes_and_validates_location(self):
        location = normalize_location(
            {
                "branch": " สาขาหลัก ",
                "floor": "ชั้น 1",
                "zone": "ทางเดิน 2",
                "map_x": "4",
                "map_y": 8,
            }
        )
        self.assertEqual(location["branch"], "สาขาหลัก")
        self.assertEqual(location["map_x"], 4)
        self.assertTrue(is_complete_location(location))

    def test_detects_relocation(self):
        previous = {
            "branch": "MAIN",
            "floor": "ชั้น 1",
            "zone": "ทางเดิน 1",
            "map_x": 2,
            "map_y": 3,
        }
        self.assertFalse(locations_differ(previous, dict(previous)))
        self.assertTrue(locations_differ(previous, dict(previous, map_x=7)))

    def test_latest_submission_is_current_location(self):
        records = [
            {
                "barcode": "8850127000016",
                "submitted_at": "2026-09-01T09:00:00+07:00",
                "location": {"branch": "MAIN", "floor": "1", "zone": "A", "map_x": 1, "map_y": 1},
            },
            {
                "barcode": "8850127000016",
                "submitted_at": "2026-09-01T10:00:00+07:00",
                "location": {"branch": "MAIN", "floor": "1", "zone": "B", "map_x": 5, "map_y": 6},
            },
        ]
        current = current_product_locations(records)
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["location"]["zone"], "B")

    def test_zone_parser_label_and_svg_are_safe(self):
        self.assertEqual(parse_store_zones("A, B, A"), ["A", "B"])
        location = {
            "branch": "<MAIN>",
            "floor": "1",
            "zone": "A&B",
            "map_x": 5,
            "map_y": 6,
        }
        self.assertIn("พิกัด 5,6", location_label(location))
        svg = store_map_svg(location)
        self.assertIn("&lt;MAIN&gt;", svg)
        self.assertNotIn("<MAIN>", svg)


if __name__ == "__main__":
    unittest.main()
