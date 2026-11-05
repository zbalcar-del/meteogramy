import unittest

import build_resorts as br


def lift(i, *pts, kind="chair_lift"):
    return {"id": i, "tags": {"aerialway": kind}, "geometry": [{"lat": a, "lon": b} for a, b in pts]}


class Resorts(unittest.TestCase):
    def test_utm_to_wgs(self):
        # Andělská hora podle ČHMÚ (EPSG:32633) -> 50,060 s. š., 17,389 v. d.
        lat, lon = br.utm33_to_wgs(670990.8047141654, 5548035.737898701)
        self.assertAlmostEqual(lat, 50.06, places=4)
        self.assertAlmostEqual(lon, 17.389, places=4)

    def test_area_follows_connected_lifts_only(self):
        base = lift(1, (50.000, 15.000), (50.004, 15.000))
        upper = lift(2, (50.0045, 15.0005), (50.010, 15.000))       # navazuje na horní stanici
        neighbour = lift(3, (50.020, 15.030), (50.024, 15.030))     # samostatný areál kousek dál
        kart = lift(4, (50.0001, 15.0001), (50.0002, 15.0002), kind="rope_tow")
        area = br.lift_area(50.0, 15.0, [base, upper, neighbour, kart], {1, 2, 3, 4})
        self.assertEqual({l["id"] for l in area}, {1, 2})

    def test_foreign_lifts_only_without_czech_ones(self):
        czech = lift(1, (50.000, 15.000), (50.004, 15.000))
        foreign = lift(2, (50.001, 15.001), (50.008, 15.001))
        self.assertEqual([l["id"] for l in br.lift_area(50.0, 15.0, [czech, foreign], {1})], [1])
        self.assertEqual([l["id"] for l in br.lift_area(50.0, 15.0, [foreign], set())], [2])


if __name__ == "__main__":
    unittest.main()
