"""
Unit tests for run.py (SkillOrchestrator).
"""

from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

# Add skill directory and scripts/ to sys.path
SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
for p in [str(SKILL_DIR), str(SCRIPTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from run import SkillOrchestrator
from trend_analyzer import (
    GrowthMetrics,
    MultiLanguageComparisonResult,
    ReliabilityBreakdown,
    SpikeMetrics,
    TrendAnalysisResult,
    VolumeMetrics,
)


class TestSkillOrchestrator(unittest.TestCase):

    def setUp(self):
        self.orchestrator = SkillOrchestrator()

        self.mock_analysis = TrendAnalysisResult(
            project="uk.wikipedia",
            lang="uk",
            article="Астрономія",
            display_title="Астрономія",
            start_date="2024-09-01",
            end_date="2026-09-01",
            days_analyzed=730,
            volume=VolumeMetrics(
                total_views=19890,
                days_count=730,
                daily_avg=27.2,
                daily_median=20.0,
                daily_max=131,
                daily_min=1,
            ),
            growth=GrowthMetrics(
                yoy_growth_percent=-55.5,
                yoy_organic_growth_percent=-57.1,
                mom_growth_percent=191.6,
                direction="declining",
            ),
            spikes=SpikeMetrics(
                q1=12.0,
                q3=36.0,
                iqr=24.0,
                spike_threshold=92.0,
                spike_days_count=11,
                spike_views_total=1165,
                spike_impact_percent=5.9,
                top_spikes=[],
            ),
            reliability=ReliabilityBreakdown(
                volume_score=13.9,
                volume_rationale="Малий обсяг.",
                stability_score=21.8,
                cv=0.64,
                stability_rationale="Помірна циклічність.",
                organic_score=29.6,
                organic_rationale="Здоровий баланс.",
                total_score=65.2,
                confidence_level="Moderate",
                verdict="Помірний рівень надійності.",
            ),
        )

        dates = pd.date_range("2024-09-01", periods=10, freq="D")
        self.mock_df = pd.DataFrame({"views": [20] * 10}, index=dates)

    @patch("trend_analyzer.TrendAnalyzer.analyze_article")
    @patch("wikimedia_client.WikimediaClient.get_article_pageviews")
    @patch("chart_generator.ChartGenerator.generate_single_article_chart")
    @patch("pdf_generator.PDFReportGenerator.generate_single_report")
    def test_run_single(
        self, mock_pdf, mock_chart, mock_pv, mock_analyze
    ):
        mock_analyze.return_value = self.mock_analysis
        mock_pv_res = MagicMock()
        mock_pv_res.to_dataframe.return_value = self.mock_df
        mock_pv.return_value = mock_pv_res

        mock_chart.return_value = Path("/tmp/chart.png")
        mock_pdf.return_value = Path("/tmp/report.pdf")

        res = self.orchestrator.run_single(
            article="Астрономія",
            project="uk",
            years=2,
            generate_pdf=True,
            generate_chart=True,
        )

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["mode"], "single_article")
        self.assertEqual(res["topic"], "Астрономія")
        self.assertEqual(res["metrics"]["total_views"], 19890)
        self.assertEqual(res["metrics"]["yoy_growth_percent"], -55.5)
        self.assertEqual(res["reliability"]["score"], 65.2)
        self.assertEqual(res["artifacts"]["chart_png"], "/tmp/chart.png")
        self.assertEqual(res["artifacts"]["report_pdf"], "/tmp/report.pdf")

    @patch("trend_analyzer.TrendAnalyzer.analyze_topic")
    @patch("wikimedia_client.WikimediaClient.get_article_pageviews")
    @patch("chart_generator.ChartGenerator.generate_multi_language_chart")
    @patch("pdf_generator.PDFReportGenerator.generate_multi_report")
    def test_run_multi(
        self, mock_pdf, mock_chart, mock_pv, mock_analyze
    ):
        mock_multi_res = MultiLanguageComparisonResult(
            topic="інтервальне голодування",
            base_lang="uk",
            wikidata_id="Q1666254",
            description="diet",
            results={"uk": self.mock_analysis},
            missing_languages=["pl"],
            comparison_summary={
                "top_market_by_volume": "uk",
                "top_market_by_growth": "uk",
                "top_market_by_reliability": "uk",
                "volume_shares_percent": {"uk": 100.0},
                "total_combined_views": 19890,
                "executive_summary": "Топ ринок: UK.",
            },
        )
        mock_analyze.return_value = mock_multi_res
        mock_pv_res = MagicMock()
        mock_pv_res.to_dataframe.return_value = self.mock_df
        mock_pv.return_value = mock_pv_res

        mock_chart.return_value = Path("/tmp/multi_chart.png")
        mock_pdf.return_value = Path("/tmp/multi_report.pdf")

        res = self.orchestrator.run_multi(
            topic="інтервальне голодування",
            langs=["uk", "pl"],
            years=2,
        )

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["mode"], "cross_lingual_comparison")
        self.assertEqual(res["topic"], "інтервальне голодування")
        self.assertIn("uk", res["languages"])
        self.assertIn("pl", res["missing_languages"])
        self.assertEqual(res["artifacts"]["chart_png"], "/tmp/multi_chart.png")
        self.assertEqual(res["artifacts"]["report_pdf"], "/tmp/multi_report.pdf")


if __name__ == "__main__":
    unittest.main()
