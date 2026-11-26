import unittest

from app.search import (
    as_card, compatibility, fill_recommendations, matches_product_variant, matching_version, merge_candidates,
    one_edit_title, preferred_categories, select_families,
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
            ("вернуть удалённые файлы с флешки", {"5922"}),
            ("подключаться к офисному пк из командировки", {"327029", "327030"}),
            ("антивирус для домашнего ноутбука", {"3775"}),
            ("запустить виртуальные машины одновременно", {"2500"}),
            ("отечественная операционная система для офиса", {"222320"}),
            ("обновить КриптоПро CSP 5.0", set()),
        ]
        for query, categories in cases:
            with self.subTest(query=query):
                self.assertEqual(preferred_categories(query), categories)


class TypoTests(unittest.TestCase):
    def test_common_one_character_typos_in_full_titles(self):
        for incorrect in ("Prgo Suite 2024", "Pro Sutite 2024", "Pro Sute 2024", "Pro Suize 2024"):
            expected = "Prog Suite 2024" if incorrect.startswith("Prgo") else "Pro Suite 2024"
            with self.subTest(incorrect=incorrect):
                self.assertTrue(one_edit_title(incorrect, expected))

    def test_numbers_never_changed_by_typo_repair(self):
        self.assertFalse(one_edit_title("Сервер Pro 2023", "Сервер Pro 2024"))
        self.assertFalse(one_edit_title("Product 501", "Product 510"))
        self.assertFalse(one_edit_title("Office Suite", "Antivirus Suite"))

class FamilyOutputTests(unittest.TestCase):
    def test_priced_variant_preferred_and_exact_sku_preserved(self):
        rows = [
            {"offer_id": "zero", "group_id": "a", "price": 0, "available": True, "exact": False},
            {"offer_id": "exact", "group_id": "b", "price": None, "available": False, "exact": True},
            {"offer_id": "priced", "group_id": "a", "price": 200, "available": True, "exact": False},
            {"offer_id": "other", "group_id": "c", "price": 50, "available": False, "exact": False},
        ]
        self.assertEqual([r["offer_id"] for r in select_families(rows)], ["exact", "priced", "other"])

    def test_identical_ungrouped_hardware_does_not_repeat(self):
        first = {"offer_id": "10", "group_id": None, "name": "Rack Server", "vendor": "DEPO",
                 "price": 90, "available": True, "exact": False}
        second = {**first, "offer_id": "11"}
        self.assertEqual(select_families([first, second]), [first])

    @staticmethod
    def card(identifier, category, platform, buyer="юрлицо"):
        return {"offer_id": identifier, "category_id": category, "group_id": identifier,
                "family_id": "group:" + identifier, "name": "Endpoint Protection",
                "available": True, "price": 100,
                "attributes": {"Платформа": platform, "Тип клиента": buyer}}

    def test_upsell_uses_catalog_platform_and_checks_buyer_type(self):
        office = self.card("office", "1145", "Windows 11")
        valid = self.card("valid", "650", "Windows 10/Windows 11")
        self.assertEqual(compatibility(office, valid, "", "650")["matched_platforms"], ["windows 11"])
        self.assertIsNone(compatibility(office, self.card("old", "650", "Windows 10"), "", "650"))
        self.assertIsNone(compatibility(office, self.card("blank", "650", ""), "", "650"))
        self.assertIsNone(compatibility(office, self.card("home", "650", "Windows 11", "физлицо"), "", "650"))
        self.assertIsNone(compatibility(office, {**valid, "name": "Mail Server Security"}, "", "650"))

    def test_verified_doprodazhi_fill_only_remaining_slots(self):
        from unittest.mock import Mock
        office = self.card("office", "1145", "Windows")
        second = self.card("another", "1145", "Windows")
        unverified = self.card("invalid", "650", "")
        verified = [self.card(f"ok-{i}", "650", "Windows") for i in range(3)]
        connection = Mock()
        connection.execute.return_value = iter([unverified, verified[0], verified[0], *verified[1:]])
        results = fill_recommendations(connection, 1, "", [office, second])
        self.assertEqual([item["offer_id"] for item in results], ["ok-0", "ok-1"])
        self.assertTrue(all(item["compatibility"]["matched_platforms"] for item in results))
