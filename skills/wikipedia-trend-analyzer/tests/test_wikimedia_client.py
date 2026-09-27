"""
Тести для модуля wikimedia_client.
"""

import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import requests

# Додавання scripts/ до sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from wikimedia_client import (
    WikimediaClient,
    DailyPageView,
    PageViewsResult,
    ArticleNotFoundError,
    RateLimitError,
    WikimediaAPIError,
)


class TestWikimediaClient(unittest.TestCase):

    def setUp(self):
        self.client = WikimediaClient(enable_cache=False)

    def test_normalize_project(self):
        self.assertEqual(WikimediaClient.normalize_project("uk"), "uk.wikipedia")
        self.assertEqual(WikimediaClient.normalize_project("uk.wikipedia.org"), "uk.wikipedia")
        self.assertEqual(WikimediaClient.normalize_project("pl.wikipedia"), "pl.wikipedia")
        self.assertEqual(WikimediaClient.normalize_project("CS"), "cs.wikipedia")

    def test_normalize_article_title(self):
        self.assertEqual(WikimediaClient.normalize_article_title("астрономія"), "Астрономія")
        self.assertEqual(WikimediaClient.normalize_article_title("intermittent fasting"), "Intermittent_fasting")
        self.assertEqual(WikimediaClient.normalize_article_title("Post_przerywany"), "Post_przerywany")
        with self.assertRaises(ValueError):
            WikimediaClient.normalize_article_title("   ")

    def test_format_timestamp(self):
        self.assertEqual(WikimediaClient.format_timestamp("2024-05-15"), "2024051500")
        self.assertEqual(WikimediaClient.format_timestamp("20240515"), "2024051500")
        self.assertEqual(WikimediaClient.format_timestamp("2024051500"), "2024051500")
        with self.assertRaises(ValueError):
            WikimediaClient.format_timestamp("invalid-date")

    def test_calculate_date_range(self):
        start, end = WikimediaClient.calculate_date_range(years=2)
        self.assertEqual(len(start), 10)
        self.assertEqual(len(end), 10)
        self.assertTrue(start < end)

    def test_pageviews_result_to_dataframe(self):
        items = [
            DailyPageView(date="2024-01-01", timestamp="2024010100", views=100),
            DailyPageView(date="2024-01-02", timestamp="2024010200", views=150),
        ]
        res = PageViewsResult(
            project="uk.wikipedia",
            article="Астрономія",
            start="2024010100",
            end="2024010200",
            granularity="daily",
            access="all-access",
            agent="user",
            items=items,
            total_views=250,
        )
        df = res.to_dataframe()
        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 2)
        self.assertEqual(df["views"].sum(), 250)
        self.assertEqual(df.index.name, "date")

    @patch.object(requests.Session, "get")
    def test_get_article_pageviews_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {"project": "uk.wikipedia", "article": "Астрономія", "granularity": "daily",
                 "timestamp": "2024010100", "access": "all-access", "agent": "user", "views": 50},
                {"project": "uk.wikipedia", "article": "Астрономія", "granularity": "daily",
                 "timestamp": "2024010200", "access": "all-access", "agent": "user", "views": 70},
            ]
        }
        mock_get.return_value = mock_resp

        res = self.client.get_article_pageviews(
            project="uk",
            article="Астрономія",
            start="2024-01-01",
            end="2024-01-02",
        )

        self.assertEqual(res.total_views, 120)
        self.assertEqual(len(res.items), 2)
        self.assertEqual(res.agent, "user")
        self.assertEqual(res.project, "uk.wikipedia")

    @patch.object(requests.Session, "get")
    def test_get_article_pageviews_404(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        with self.assertRaises(ArticleNotFoundError):
            self.client.get_article_pageviews(
                project="uk",
                article="НеіснуючаСтаття12345",
                start="2024-01-01",
                end="2024-01-02",
            )

    @patch.object(requests.Session, "get")
    def test_get_article_pageviews_429(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_get.return_value = mock_resp

        with self.assertRaises(RateLimitError):
            self.client.get_article_pageviews(
                project="uk",
                article="Астрономія",
                start="2024-01-01",
                end="2024-01-02",
            )


if __name__ == "__main__":
    unittest.main()
