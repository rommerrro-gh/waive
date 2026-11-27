import unittest
from unittest.mock import Mock, patch

from app.e5_spelling import E5Spelling
from app.relations import category_coverage, comparable_offer, compatibility_evidence
from app.requirements import parse_requirements, unmet_requirements
from app.spelling import clear_winner, rank_spellings
from app.vendor_spellings import VendorSpellings

from app.search import (
    as_card, is_kaspersky_query, matches_product_variant, matching_version, merge_candidates,
    one_edit_title, preferred_categories, recommend_for_anchor, resolve_catalog_query, resolve_search_input, resolve_suggestion_alias,
    select_families, vpn_offer, vpn_requested,
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
    def test_unusable_model_leaves_catalog_repair_operational(self):
        for embed in (Mock(side_effect=TimeoutError), Mock(return_value=("old", []))):
            with self.subTest(embed=embed), \
                    patch("app.search.catalog_spelling_options", return_value=[(8, "Kaspersky")]), \
                    patch("app.search.lexical_candidates", side_effect=[[], [{"vendor": "Kaspersky"}]]):
                speller = E5Spelling({}, "current", embed)
                decision = resolve_catalog_query(None, 1, "Kaspreskky", prefixes=[], speller=speller)
                self.assertEqual(decision.lookup, "Kaspersky")
                embed.assert_called_once()

    def test_vpn_normalization_keeps_surrounding_words(self):
        self.assertEqual(resolve_search_input("ВПН"), "VPN")
        self.assertEqual(resolve_search_input("Kaspersky впн"), "Kaspersky VPN")
        self.assertEqual(resolve_search_input("впнтермин"), "впнтермин")
        self.assertEqual(resolve_search_input("виртуальная частная сеть"), "VPN")

    def test_vpn_purpose_requires_explicit_product_evidence(self):
        self.assertTrue(vpn_requested("корпоративный VPN"))
        self.assertTrue(vpn_requested("Kaspersky VPN"))
        self.assertFalse(vpn_requested("видеонаблюдение через VPN"))
        self.assertFalse(vpn_offer({"name": "IPVideoRecord Видеонаблюдение", "description": "доступ через VPN"}, "VPN"))
        self.assertFalse(vpn_offer({"name": "F-Secure Elements Without VPN"}, "VPN"))
        self.assertFalse(vpn_offer({"name": "МагПро OpenVPN (формуляр)"}, "VPN"))
        self.assertTrue(vpn_offer({"name": "МагПро OpenVPN ГОСТ, лицензия"}, "VPN"))
        self.assertTrue(vpn_offer({"name": "ViPNet Client"}, "VPN"))
        self.assertFalse(vpn_offer({"name": "ViPNet Client", "vendor": "ИнфоТеКС"}, "Kaspersky VPN"))

    def test_e5_recovers_a_catalog_brand_and_preserves_version(self):
        embed = Mock(side_effect=lambda kind, names, timeout: ("current", [[1.0, 0.0] for name in names]))
        speller = E5Spelling({"AnyDesk": ("анидэск", "энидэск", "ани деск")}, "current", embed)
        self.assertEqual(speller.suggest("адинэсс VPN 5.0", []), ["AnyDesk VPN 5.0"])
        self.assertEqual(speller.suggest("землекоп", []), [])
        self.assertEqual(speller.suggest("KL8072RMZZZ", []), [])
        self.assertTrue(all(call.args[0] == "query" for call in embed.call_args_list))

    def test_catalog_vendor_candidates_require_e5_and_keep_the_suffix(self):
        index = VendorSpellings(["Cactus", "Canon", "Dell Technologies", "Kyocera", "Panasonic"])
        self.assertEqual(index.candidates("кактус, картридж 5.0"),
                         ("кактус", ", картридж 5.0", ["Cactus"], True))
        self.assertEqual(index.candidates("какту", complete_prefix=True),
                         ("какту", "", ["Cactus"], False))
        self.assertEqual(index.candidates("делл монитор")[2], ["Dell Technologies"])
        self.assertEqual(index.candidates("киосера")[2], ["Kyocera"])
        embed = Mock(return_value=("current", [[1., 0.], [1., 0.]]))
        speller = E5Spelling({}, "current", embed)
        item = {"vendor": "Cactus"}
        with patch("app.search.lexical_candidates", side_effect=[[], [item]]):
            decision = resolve_catalog_query(
                None, 1, "кактус картридж 5.0", prefixes=[], speller=speller,
                vendor_index=lambda *_: index,
            )
        self.assertEqual((decision.lookup, decision.vendor), ("Cactus картридж 5.0", "Cactus"))
        embed.assert_called_once_with("query", ["кактус", "Cactus"], .4)

    def test_vendor_ranking_filters_weak_matches(self):
        embed = Mock(return_value=("current", [[1., 0.], [.1, .995], [1., 0.]]))
        speller = E5Spelling({}, "current", embed)
        self.assertEqual(speller.rank_vendors("канон", ["Kanon", "Canon"]), [(1.0, "Canon")])

    def test_multiple_edits_and_ambiguity(self):
        ranked = rank_spellings("каспрсикй", [("Kaspersky", "касперский"), ("Cactus", "кактус")])
        self.assertEqual([label for _, label in ranked], ["Kaspersky"])
        self.assertTrue(clear_winner(ranked))
        ambiguous = rank_spellings("office zuite", [("Office Suite", "office suite"), ("Office Quite", "office quite")])
        self.assertEqual(len(ambiguous), 2)
        self.assertFalse(clear_winner(ambiguous))
        self.assertEqual(rank_spellings("криптопр csp 5.0", [("КриптоПро CSP 4.0", "КриптоПро CSP 4.0")]), [])

    def test_common_one_character_typos_in_full_titles(self):
        for incorrect in ("Prgo Suite 2024", "Pro Sutite 2024", "Pro Sute 2024", "Pro Suize 2024"):
            expected = "Prog Suite 2024" if incorrect.startswith("Prgo") else "Pro Suite 2024"
            with self.subTest(incorrect=incorrect):
                self.assertTrue(one_edit_title(incorrect, expected))

    def test_numbers_never_changed_by_typo_repair(self):
        self.assertFalse(one_edit_title("Сервер Pro 2023", "Сервер Pro 2024"))
        self.assertFalse(one_edit_title("Product 501", "Product 510"))
        self.assertFalse(one_edit_title("Office Suite", "Antivirus Suite"))

    def test_short_brand_queries_allow_one_edit_without_capturing_product_queries(self):
        for query in ("Kaspersky", "Касперский", "Каспер", "Лаборатория Касперского", "касперчкий", "касперсикй", "Kaspresky"):
            with self.subTest(query=query):
                self.assertTrue(is_kaspersky_query(query))
        for query in ("касперчк", "касперский 2024", "Kaspersky Small Office Security", "игровой коврик"):
            with self.subTest(query=query):
                self.assertFalse(is_kaspersky_query(query))

    def test_suggestion_aliases_handle_prefixes_without_matching_unrelated_words(self):
        for query, expected in (
            ("майкр", "Microsoft"), ("Доктор Веб", "Dr.Web"), ("анидеск", "AnyDesk"),
            ("винрар", "WinRAR"), ("астра", "Astra"), ("астра лин", "Astra Linux"),
            ("май", None), ("микрофон", None), ("астр", None), ("AnyD", None),
        ):
            with self.subTest(query=query):
                self.assertEqual(resolve_suggestion_alias(query), expected)

    def test_registered_brand_forms_preserve_qualifiers_and_recognize_keyboard_layout(self):
        for query, expected in (
            ("rfcgthcrbq", "Kaspersky"), ("фтнвуыл", "AnyDesk"), ("Any Desk", "AnyDesk"),
            ("майкр", "Microsoft"), ("майкрософт Visio 2024", "Microsoft Visio 2024"),
            ("доктор веб CureNet", "Dr.Web CureNet"), ("Астра Линукс SE", "Astra Linux SE"),
            ("Астра сервер", "Астра сервер"), ("zqjy939", "zqjy939"),
        ):
            with self.subTest(query=query):
                self.assertEqual(resolve_search_input(query), expected)

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
        coverage = category_coverage({"1145": ("Офисные программы", None), "650": ("Антивирусы", None)}, {"1145", "650"})
        connection = Mock()
        connection.execute.return_value = [
            self.card("old", "650", "Windows 10"), self.card("blank", "650", ""),
            self.card("home", "650", "Windows 11", "физлицо"),
            {**valid, "offer_id": "mail", "name": "Mail Server Security"}, valid,
        ]
        items, status = recommend_for_anchor(connection, 1, office, coverage)
        self.assertEqual(status, "confirmed")
        self.assertEqual([item["offer_id"] for item in items], ["valid"])
        self.assertEqual(items[0]["compatibility"]["matched_platforms"], ["windows 11"])

    def test_verified_doprodazhi_deduplicate_and_respect_limit(self):
        office = self.card("office", "1145", "Windows")
        unverified = self.card("invalid", "650", "")
        verified = [self.card(f"ok-{i}", "650", "Windows") for i in range(3)]
        coverage = category_coverage({"1145": ("Офисные программы", None), "650": ("Антивирусы", None)}, {"1145", "650"})
        connection = Mock()
        connection.execute.return_value = iter([unverified, verified[0], verified[0], *verified[1:]])
        results, _ = recommend_for_anchor(connection, 1, office, coverage, limit=2)
        self.assertEqual([item["offer_id"] for item in results], ["ok-0", "ok-1"])
        self.assertTrue(all(item["compatibility"]["matched_platforms"] for item in results))


class CatalogRequirementsTests(unittest.TestCase):
    def test_requirements_must_apply_to_one_offer(self):
        query = "Защита для Windows 11, на 50 рабочих мест, без подписки, до 12 000 ₽"
        requirements = parse_requirements(query)
        attributes = {"Платформа": "Windows 11", "К-во узлов": "50", "Тип лицензии": "Полная версия"}
        row = {"name": "Endpoint Protection", "price": 10_000, "currency": "RUB", "available": True}
        self.assertFalse(unmet_requirements(row, attributes, requirements))
        self.assertTrue(unmet_requirements(row, {**attributes, "Тип лицензии": "Подписка"}, requirements))
        self.assertTrue(unmet_requirements(row, {**attributes, "Платформа": "Windows"}, requirements))
        failures = unmet_requirements(row, {**attributes, "К-во узлов": "", "Минимальная покупка": "50"}, requirements)
        self.assertTrue(any(requirement.key == "devices" and state is None for requirement, state in failures))

    def test_platform_as_product_name_does_not_exclude_operating_systems(self):
        self.assertFalse(parse_requirements("Astra Linux"))
        self.assertEqual(preferred_categories("Astra Linux"), {"222320"})

    def test_explicit_alternative_platforms_accept_either_confirmed_os(self):
        requirements = parse_requirements("антивирус для Windows 11 или macOS")
        offer = {"name": "Endpoint Security"}
        self.assertFalse(unmet_requirements(offer, {"Платформа": "macOS"}, requirements))
        self.assertFalse(unmet_requirements(offer, {"Платформа": "Windows 11"}, requirements))
        self.assertTrue(unmet_requirements(offer, {"Платформа": "Windows 10"}, requirements))

    def test_preference_does_not_exclude_candidate(self):
        requirements = parse_requirements("антивирус, желательно до 5 000 ₽")
        self.assertEqual([(req.key, req.mandatory) for req in requirements], [("price_max", False)])
        self.assertFalse(unmet_requirements({"name": "Антивирус", "price": 6000, "currency": "RUB"}, {}, requirements))

    def test_physical_and_product_specific_compatibility_require_evidence(self):
        def card(name, **attributes):
            return {"name": name, "attributes": attributes}

        self.assertTrue(compatibility_evidence(card("Ноутбук", **{"Разъёмы USB 3.2": "2"}),
                                               card("Клавиатура", Интерфейс="USB"), "usb"))
        self.assertIsNone(compatibility_evidence(card("Ноутбук", **{"Разъёмы Type-C": "1"}),
                                                card("Док", Интерфейс="USB-C"), "dock"))
        printer = card("HP LaserJet M404dn")
        self.assertTrue(compatibility_evidence(printer, card("Тонер", **{"Модель принтера": "M404dn"}), "printer"))
        self.assertIsNone(compatibility_evidence(printer, card("Тонер", **{"Модель принтера": "M404"}), "printer"))
        self.assertFalse(comparable_offer(card("Тонер", **{"Модель принтера": "M404dn"}),
                                          card("Тонер", **{"Модель принтера": "M404"})))
