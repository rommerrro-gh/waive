import unittest

from app.procurement import filter_offers, purchase_values, requested_filters


class PurchaseTests(unittest.TestCase):
    def test_suggest_confirmed_parts_of_query(self):
        self.assertEqual(requested_filters("антивирус для Windows Server на год"),
                         {"platform": "windows-server", "term": "12"})
        self.assertEqual(requested_filters("продление подписки на 3 года"),
                         {"term": "36", "license_type": "renewal"})
        self.assertEqual(requested_filters("антивирус для AstraLinux"), {"platform": "astra-linux"})

    def test_platform_and_license_variants_are_distinct(self):
        self.assertEqual(purchase_values({
            "Платформа": "Windows Server 2022 / AstraLinux",
            "Тип лицензии": "Перекрестное обновление, техподдержка",
            "Срок действия": "бессрочная",
        }), {"platform": {"windows-server", "linux", "astra-linux"},
             "term": {"perpetual"}, "license_type": {"upgrade"}})

    def test_every_condition_must_be_matched_by_the_same_offer(self):
        offers = [
            {"family": "A", "attrs": {"Платформа": "Windows", "Срок действия": "24 мес."}},
            {"family": "A", "attrs": {"Платформа": "Linux", "Срок действия": "12 мес."}},
            {"family": "B", "attrs": {"Платформа": "Windows", "Срок действия": "Не определено"}},
            {"family": "C", "attrs": {"Платформа": "Windows 11", "Срок действия": "12 мес."}},
        ]
        passed, info = filter_offers(offers, {"platform": "windows", "term": "12"},
                                     lambda item: item["family"], lambda item: item["attrs"])
        self.assertEqual(passed, [offers[-1]])
        self.assertEqual(info, {"families_before": 3, "families_after": 1, "conditions": {
            "platform": {"would_restore": 1, "unverified": 0},
            "term": {"would_restore": 2, "unverified": 1},
        }})

    def test_stock_and_ruble_bounds_apply_to_a_single_offer(self):
        priced = {"family": "A", "price": 2400, "currency": "RUR", "available": True}
        out_of_stock = {**priced, "family": "B", "available": False}
        too_expensive = {**priced, "family": "C", "price": 2401}
        unknown = {**priced, "family": "D", "price": None}
        foreign_currency = {**priced, "family": "E", "currency": "USD"}
        passed, diagnostics = filter_offers(
            [priced, out_of_stock, too_expensive, unknown, foreign_currency],
            {"in_stock": True, "price_max": 2400},
            lambda item: item["family"], lambda item: {},
        )
        self.assertEqual(passed, [priced])
        self.assertEqual(diagnostics["conditions"], {
            "in_stock": {"would_restore": 1, "unverified": 0},
            "price_max": {"would_restore": 3, "unverified": 2},
        })
