import unittest

from app.search import (
    as_card, diversify, matches_product_variant, matching_version, merge_candidates,
    preferred_categories,
)


class VersionTests(unittest.TestCase):
    def test_explicit_version_overrides_name(self):
        item = {"attributes": {"Версия": "4.0"}, "name": "Обновление для 5.0"}
        self.assertFalse(matching_version(item, {"5.0"}))

    def test_name_provides_version_when_attribute_is_missing(self):
        item = {"attributes": {}, "name": "КриптоПро CSP 5.0"}
        self.assertTrue(matching_version(item, {"5.0"}))
        self.assertFalse(matching_version(item, {"4.0"}))


class RankingTests(unittest.TestCase):
    def test_prefer_semantic_candidate_for_description(self):
        lexical = {"offer_id": "1", "group_id": "a", "exact": False, "available": True}
        semantic = {"offer_id": "2", "group_id": "b", "exact": False, "available": True}
        self.assertEqual(merge_candidates([lexical], [semantic], 0.65)[0], semantic)

    def test_exact_article_preserves_priority(self):
        exact = {"offer_id": "1", "group_id": "a", "exact": True, "available": True}
        semantic = {"offer_id": "2", "group_id": "b", "exact": False, "available": True}
        self.assertEqual(merge_candidates([exact], [semantic], 0.65)[0], exact)

class ProductCardTests(unittest.TestCase):
    def test_tls_extension_is_not_a_base_license(self):
        item = {"exact": False, "name": "КриптоПро CSP TLS", "attributes": {"Версия": "5.0"}}
        self.assertFalse(matches_product_variant(item, "КриптоПро CSP 5.0", {"5.0"}))
        self.assertTrue(matches_product_variant(item, "КриптоПро CSP TLS 5.0", {"5.0"}))

    def test_minimum_purchase_is_visible_on_the_card(self):
        item = {"name": "O&amp;O", "attributes": {}, "price": 100, "exact": False,
                "description": "Тип лицензии: полная • Минимальная покупка: от 10 до 49 шт."}
        card = as_card(item, "Совпадение")
        self.assertEqual(card["name"], "O&O")
        self.assertEqual(card["attributes"]["Минимальная покупка"], "от 10 до 49 шт.")
        self.assertNotIn("description", card)

class IntentTests(unittest.TestCase):
    def test_paraphrases_match_catalog_categories(self):
        cases = [
            ("создать копию документов на случай сбоя", {"1033"}),
            ("защитить рабочие станции компании от вредоносных вирусов", {"650"}),
            ("подбор оборудования для монтажа в серверную стойку", {"86563"}),
            ("хочу редактировать текст и таблицы на работе", {"1145", "222323"}),
            ("обновить КриптоПро CSP 5.0", set()),
        ]
        for query, categories in cases:
            with self.subTest(query=query):
                self.assertEqual(preferred_categories(query), categories)

    def test_diversify_fills_from_other_families_before_variants(self):
        variants = [
            {"offer_id": index, "group_id": family, "exact": exact}
            for index, family, exact in [(10, "a", True), (11, "a", False),
                                          (12, "b", False), (13, None, False)]
        ]
        self.assertEqual([item["offer_id"] for item in diversify(variants)], [10, 12, 13, 11])
