"""
Unit tests for chart_generator and pdf_generator modules.
"""

from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

# Add scripts/ to sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from chart_generator import ChartGenerator
from pdf_generator import PDFReportGenerator
from trend_analyzer import (
    GrowthMetrics,
    MultiLanguageComparisonResult,
    ReliabilityBreakdown,
    SpikeEvent,
    SpikeMetrics,
    TrendAnalysisResult,
    VolumeMetrics,
)


class TestVisualAndPDF(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.chart_gen = ChartGenerator(output_dir=self.tmp_path)
        self.pdf_gen = PDFReportGenerator(output_dir=self.tmp_path)

        # Mock TrendAnalysisResult
        self.mock_analysis = TrendAnalysisResult(
            project="uk.wikipedia",
            lang="uk",
            article="Астрономія",
            display_title="Астрономія",
            start_date="2024-09-01",
            end_date="2026-09-01",
            days_analyzed=730,
            volume=VolumeMetrics(
                total_views=50000,
                days_count=730,
                daily_avg=68.5,
                daily_median=50.0,
                daily_max=350,
                daily_min=10,
                recent_year_views=28000,
                prior_year_views=22000,
            ),
            growth=GrowthMetrics(
                yoy_growth_percent=27.27,
                yoy_organic_growth_percent=25.0,
                mom_growth_percent=12.5,
                direction="growing",
            ),
            spikes=SpikeMetrics(
                q1=30.0,
                q3=75.0,
                iqr=45.0,
                spike_threshold=185.0,
                spike_days_count=4,
                spike_views_total=1100,
                spike_impact_percent=2.2,
                top_spikes=[
                    SpikeEvent(
                        date="2025-04-08",
                        views=350,
                        median=50.0,
                        ratio_to_median=7.0,
                        threshold=185.0,
                    )
                ],
            ),
            reliability=ReliabilityBreakdown(
                volume_score=23.2,
                volume_rationale="Помірний обсяг трафіку.",
                stability_score=26.0,
                cv=0.42,
                stability_rationale="Добра стабільність інтересу.",
                organic_score=30.0,
                organic_rationale="Органічний інтерес без спалахів.",
                total_score=79.2,
                confidence_level="Moderate",
                verdict="Стійкий органічний ріст з помірною довірою.",
            ),
        )

        # Mock DataFrame
        dates = pd.date_range(start="2024-09-01", periods=730, freq="D")
        np.random.seed(42)
        views = np.random.randint(20, 100, size=730)
        views[100] = 350
        self.mock_df = pd.DataFrame({"views": views}, index=dates)

        # Mock MultiLanguageComparisonResult
        mock_analysis_pl = TrendAnalysisResult(
            project="pl.wikipedia",
            lang="pl",
            article="Astronomia",
            display_title="Astronomia",
            start_date="2024-09-01",
            end_date="2026-09-01",
            days_analyzed=730,
            volume=VolumeMetrics(
                total_views=120000,
                days_count=730,
                daily_avg=164.3,
                daily_median=140.0,
                daily_max=800,
                daily_min=40,
            ),
            growth=GrowthMetrics(
                yoy_growth_percent=15.0,
                yoy_organic_growth_percent=14.0,
                mom_growth_percent=5.0,
                direction="growing",
            ),
            spikes=SpikeMetrics(
                q1=100.0,
                q3=200.0,
                iqr=100.0,
                spike_threshold=440.0,
                spike_days_count=3,
                spike_views_total=2100,
                spike_impact_percent=1.75,
                top_spikes=[],
            ),
            reliability=ReliabilityBreakdown(
                volume_score=32.0,
                volume_rationale="Достатній обсяг.",
                stability_score=27.0,
                cv=0.38,
                stability_rationale="Добра стабільність.",
                organic_score=30.0,
                organic_rationale="Органічний інтерес.",
                total_score=89.0,
                confidence_level="High",
                verdict="Висока довіра до польського ринку.",
            ),
        )

        self.mock_multi_res = MultiLanguageComparisonResult(
            topic="Астрономія",
            base_lang="uk",
            wikidata_id="Q333",
            description="наука про Всесвіт",
            results={"uk": self.mock_analysis, "pl": mock_analysis_pl},
            missing_languages=[],
            comparison_summary={
                "top_market_by_volume": "pl",
                "top_market_by_growth": "uk",
                "top_market_by_reliability": "pl",
                "volume_shares_percent": {"uk": 29.4, "pl": 70.6},
                "total_combined_views": 170000,
                "executive_summary": "Найбільший ринок: PL. Найвища динаміка: UK.",
            },
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_generate_single_article_chart(self):
        chart_file = self.chart_gen.generate_single_article_chart(
            self.mock_analysis, self.mock_df
        )
        self.assertTrue(chart_file.exists())
        self.assertGreater(chart_file.stat().st_size, 10000)

    def test_generate_multi_language_chart(self):
        dfs = {"uk": self.mock_df, "pl": self.mock_df * 2}
        chart_file = self.chart_gen.generate_multi_language_chart(
            self.mock_multi_res, dfs
        )
        self.assertTrue(chart_file.exists())
        self.assertGreater(chart_file.stat().st_size, 10000)

    def test_generate_single_pdf_report(self):
        chart_file = self.chart_gen.generate_single_article_chart(
            self.mock_analysis, self.mock_df
        )
        pdf_file = self.pdf_gen.generate_single_report(self.mock_analysis, chart_file)

        self.assertTrue(pdf_file.exists())
        self.assertGreater(pdf_file.stat().st_size, 20000)

    def test_generate_multi_pdf_report(self):
        dfs = {"uk": self.mock_df, "pl": self.mock_df * 2}
        chart_file = self.chart_gen.generate_multi_language_chart(
            self.mock_multi_res, dfs
        )
        pdf_file = self.pdf_gen.generate_multi_report(self.mock_multi_res, chart_file)

        self.assertTrue(pdf_file.exists())
        self.assertGreater(pdf_file.stat().st_size, 20000)


if __name__ == "__main__":
    unittest.main()
