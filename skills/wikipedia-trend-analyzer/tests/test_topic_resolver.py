"""
Unit tests for topic_resolver module.
"""

from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add scripts/ to sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from topic_resolver import (
    ResolvedArticle,
    TopicResolutionResult,
    TopicResolver,
    parse_override_arg,
)


class TestTopicResolver(unittest.TestCase):

    def setUp(self):
        self.resolver = TopicResolver(enable_cache=False)

    def test_detect_base_lang(self):
        self.assertEqual(TopicResolver.detect_base_lang("інтервальне голодування"), "uk")
        self.assertEqual(TopicResolver.detect_base_lang("Астрономія"), "uk")
        self.assertEqual(TopicResolver.detect_base_lang("intermittent fasting"), "en")
        self.assertEqual(TopicResolver.detect_base_lang("astronomy"), "en")
        self.assertEqual(TopicResolver.detect_base_lang("12345"), "en")

    def test_parse_override_arg(self):
        self.assertEqual(parse_override_arg(None), {})
        self.assertEqual(parse_override_arg(""), {})
        
        single = parse_override_arg("pl:Głodówka_lecznicza")
        self.assertEqual(single, {"pl": "Głodówka_lecznicza"})

        multiple = parse_override_arg("pl:Głodówka_lecznicza, cs:Půst , UK:Інтервальне_голодування")
        self.assertEqual(
            multiple,
            {
                "pl": "Głodówka_lecznicza",
                "cs": "Půst",
                "uk": "Інтервальне_голодування",
            },
        )

    @patch("requests.Session.get")
    def test_search_wikipedia_article_opensearch(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            "астрономія",
            ["Астрономія"],
            ["Наука про Всесвіт"],
            ["https://uk.wikipedia.org/wiki/Астрономія"],
        ]
        mock_get.return_value = mock_resp

        title = self.resolver.search_wikipedia_article("астрономія", lang="uk")
        self.assertEqual(title, "Астрономія")

    @patch("requests.Session.get")
    def test_search_wikipedia_article_fallback(self, mock_get):
        # First call (opensearch) returns empty list; second call (search query) returns title
        mock_resp1 = MagicMock()
        mock_resp1.status_code = 200
        mock_resp1.json.return_value = ["test", [], [], []]

        mock_resp2 = MagicMock()
        mock_resp2.status_code = 200
        mock_resp2.json.return_value = {
            "query": {"search": [{"title": "Fallback Title"}]}
        }

        mock_get.side_effect = [mock_resp1, mock_resp2]

        title = self.resolver.search_wikipedia_article("test", lang="en")
        self.assertEqual(title, "Fallback Title")

    @patch("requests.Session.get")
    def test_search_wikipedia_article_not_found(self, mock_get):
        mock_resp1 = MagicMock()
        mock_resp1.status_code = 200
        mock_resp1.json.return_value = ["nonexistent", [], [], []]

        mock_resp2 = MagicMock()
        mock_resp2.status_code = 200
        mock_resp2.json.return_value = {"query": {"search": []}}

        mock_get.side_effect = [mock_resp1, mock_resp2]

        title = self.resolver.search_wikipedia_article("nonexistent", lang="en")
        self.assertIsNone(title)

    @patch("requests.Session.get")
    def test_get_wikidata_item_id(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "query": {
                "pages": {
                    "12345": {
                        "pageid": 12345,
                        "title": "Інтервальне голодування",
                        "pageprops": {"wikibase_item": "Q1666254"},
                    }
                }
            }
        }
        mock_get.return_value = mock_resp

        qid = self.resolver.get_wikidata_item_id("Інтервальне голодування", lang="uk")
        self.assertEqual(qid, "Q1666254")

    @patch("requests.Session.get")
    def test_get_wikidata_sitelinks_and_desc(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "entities": {
                "Q1666254": {
                    "id": "Q1666254",
                    "descriptions": {
                        "en": {"language": "en", "value": "dietary practice"},
                        "uk": {"language": "uk", "value": "дієтична практика"},
                    },
                    "sitelinks": {
                        "ukwiki": {"site": "ukwiki", "title": "Інтервальне голодування"},
                        "cswiki": {"site": "cswiki", "title": "Přerušovaný půst"},
                        "enwiki": {"site": "enwiki", "title": "Intermittent fasting"},
                        "commonswiki": {"site": "commonswiki", "title": "Category:Intermittent fasting"},
                    },
                }
            }
        }
        mock_get.return_value = mock_resp

        sitelinks, desc = self.resolver.get_wikidata_sitelinks_and_desc("Q1666254")
        self.assertEqual(desc, "дієтична практика")
        self.assertIn("uk", sitelinks)
        self.assertEqual(sitelinks["uk"], "Інтервальне голодування")
        self.assertIn("cs", sitelinks)
        self.assertEqual(sitelinks["cs"], "Přerušovaný půst")
        self.assertIn("en", sitelinks)
        self.assertNotIn("commons", sitelinks)  # Filtered out non-wikipedia projects

    @patch("requests.Session.get")
    def test_search_candidates_in_lang(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            "post przerywany",
            ["Post", "Głodówka", "Post leczniczy"],
            [],
            [],
        ]
        mock_get.return_value = mock_resp

        candidates = self.resolver.search_candidates_in_lang("post przerywany", "pl", limit=3)
        self.assertEqual(candidates, ["Post", "Głodówka", "Post leczniczy"])

    @patch.object(TopicResolver, "search_candidates_in_lang")
    @patch.object(TopicResolver, "get_wikidata_sitelinks_and_desc")
    @patch.object(TopicResolver, "get_wikidata_item_id")
    @patch.object(TopicResolver, "search_wikipedia_article")
    def test_resolve_with_missing_and_candidates(
        self, mock_search, mock_get_qid, mock_get_links, mock_candidates
    ):
        mock_search.return_value = "Інтервальне голодування"
        mock_get_qid.return_value = "Q1666254"
        mock_get_links.return_value = (
            {
                "uk": "Інтервальне голодування",
                "cs": "Přerušovaný půst",
                # Note: plwiki is missing intentionally
            },
            "опис теми",
        )
        mock_candidates.return_value = ["Głodówka lecznicza", "Post"]

        res = self.resolver.resolve(
            topic="інтервальне голодування",
            target_langs=["uk", "cs", "pl"],
        )

        self.assertEqual(res.base_lang, "uk")
        self.assertEqual(res.wikidata_id, "Q1666254")
        self.assertEqual(res.get_article_title("uk"), "Інтервальне_голодування")
        self.assertEqual(res.get_article_title("cs"), "Přerušovaný_půst")
        self.assertIsNone(res.get_article_title("pl"))

        self.assertEqual(res.get_found_languages(), ["uk", "cs"])
        self.assertEqual(res.get_missing_languages(), ["pl"])

        pl_article = res.articles["pl"]
        self.assertFalse(pl_article.found)
        self.assertEqual(pl_article.source, "not_found")
        self.assertIn("Głodówka lecznicza", pl_article.candidates)

    @patch.object(TopicResolver, "search_candidates_in_lang")
    @patch.object(TopicResolver, "get_wikidata_sitelinks_and_desc")
    @patch.object(TopicResolver, "get_wikidata_item_id")
    @patch.object(TopicResolver, "search_wikipedia_article")
    def test_resolve_with_override(
        self, mock_search, mock_get_qid, mock_get_links, mock_candidates
    ):
        mock_search.return_value = "Інтервальне голодування"
        mock_get_qid.return_value = "Q1666254"
        mock_get_links.return_value = (
            {"uk": "Інтервальне голодування", "cs": "Přerušovaný půst"},
            "опис",
        )

        res = self.resolver.resolve(
            topic="інтервальне голодування",
            target_langs=["pl", "cs"],
            overrides={"pl": "Głodówka_lecznicza"},
        )

        self.assertTrue(res.articles["pl"].found)
        self.assertEqual(res.articles["pl"].source, "manual_override")
        self.assertEqual(res.get_article_title("pl"), "Głodówka_lecznicza")
        self.assertEqual(res.get_article_title("cs"), "Přerušovaný_půst")
        mock_candidates.assert_not_called()


if __name__ == "__main__":
    unittest.main()
