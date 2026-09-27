"""
Unit tests for trend_analyzer module.
"""

from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

# Add scripts/ to sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from trend_analyzer import (
    GrowthMetrics,
    MultiLanguageComparisonResult,
    ReliabilityBreakdown,
    SpikeMetrics,
    TrendAnalysisResult,
    TrendAnalyzer,
    VolumeMetrics,
)


class TestTrendAnalyzer(unittest.TestCase):

    def setUp(self):
        self.analyzer = TrendAnalyzer()

    def _generate_synthetic_df(
        self, days: int = 730, base_val: float = 100.0, trend: float = 0.0, noise: float = 5.0
    ) -> pd.DataFrame:
        """Генерує синтетичний часовий ряд переглядів."""
        dates = pd.date_range(end="2026-09-01", periods=days, freq="D")
        np.random.seed(42)
        trend_line = np.linspace(0, trend, days)
        noise_vals = np.random.normal(0, noise, days)
        views = np.maximum(1, np.round(base_val + trend_line + noise_vals)).astype(int)
        return pd.DataFrame({"views": views}, index=dates)

    def test_analyze_dataframe_volume_and_growth(self):
        # 730 днів: перший рік ~100/день, другий рік ~200/день (+100% YoY)
        df1 = self._generate_synthetic_df(days=365, base_val=100.0)
        df2 = self._generate_synthetic_df(days=365, base_val=200.0)
        dates = pd.date_range(start="2024-09-01", periods=730, freq="D")
        combined_views = np.concatenate([df1["views"].values, df2["views"].values])
        df = pd.DataFrame({"views": combined_views}, index=dates)

        res = self.analyzer.analyze_dataframe(df, project="uk.wikipedia", article="Тест")

        self.assertEqual(res.days_analyzed, 730)
        self.assertAlmostEqual(res.volume.daily_avg, 150.0, delta=10.0)
        self.assertIsNotNone(res.growth.yoy_growth_percent)
        # YoY має бути близько +100%
        self.assertGreater(res.growth.yoy_growth_percent, 80.0)
        self.assertEqual(res.growth.direction, "growing")

    def test_spike_detection(self):
        # 100 днів по 50 переглядів, і 3 аномальні дні по 500 переглядів
        dates = pd.date_range(start="2026-01-01", periods=100, freq="D")
        views = np.full(100, 50)
        views[10] = 500
        views[30] = 600
        views[60] = 700
        df = pd.DataFrame({"views": views}, index=dates)

        res = self.analyzer.analyze_dataframe(df, project="uk.wikipedia", article="Спайки")

        self.assertEqual(res.spikes.spike_days_count, 3)
        self.assertEqual(res.spikes.spike_views_total, 1800)
        self.assertGreater(res.spikes.spike_impact_percent, 20.0)
        self.assertEqual(len(res.spikes.top_spikes), 3)
        self.assertEqual(res.spikes.top_spikes[0].views, 700)
        self.assertGreater(res.spikes.top_spikes[0].ratio_to_median, 10.0)

    def test_reliability_score_high_confidence(self):
        # Великий трафік (>500/день), стабільний, без спалахів
        dates = pd.date_range(start="2024-09-01", periods=730, freq="D")
        views = np.full(730, 800)  # ідеально стабільний
        df = pd.DataFrame({"views": views}, index=dates)

        res = self.analyzer.analyze_dataframe(df, project="en.wikipedia", article="HighConfidence")

        self.assertEqual(res.reliability.volume_score, 40.0)
        self.assertEqual(res.reliability.confidence_level, "High")
        self.assertGreaterEqual(res.reliability.total_score, 85.0)

    def test_reliability_score_low_confidence_small_numbers(self):
        # Критично малий трафік (10 переглядів/день)
        dates = pd.date_range(start="2024-09-01", periods=730, freq="D")
        views = np.random.randint(1, 15, size=730)
        df = pd.DataFrame({"views": views}, index=dates)

        res = self.analyzer.analyze_dataframe(df, project="uk.wikipedia", article="SmallTraffic")

        self.assertLess(res.reliability.volume_score, 15.0)
        self.assertIn(res.reliability.confidence_level, ["Moderate", "Low"])

    def test_empty_and_invalid_dataframe(self):
        with self.assertRaises(ValueError):
            self.analyzer.analyze_dataframe(pd.DataFrame(), project="uk.wikipedia", article="Empty")

        with self.assertRaises(ValueError):
            invalid_df = pd.DataFrame({"wrong_column": [1, 2, 3]})
            self.analyzer.analyze_dataframe(invalid_df, project="uk.wikipedia", article="Invalid")

    def test_multi_language_synthesis(self):
        df_pl = self._generate_synthetic_df(days=730, base_val=300.0, trend=50.0)
        df_cs = self._generate_synthetic_df(days=730, base_val=100.0, trend=-20.0)

        res_pl = self.analyzer.analyze_dataframe(df_pl, project="pl.wikipedia", article="PL_Topic")
        res_cs = self.analyzer.analyze_dataframe(df_cs, project="cs.wikipedia", article="CS_Topic")

        results = {"pl": res_pl, "cs": res_cs}
        summary = self.analyzer._synthesize_comparison("тема", results, missing_langs=[])

        self.assertEqual(summary["top_market_by_volume"], "pl")
        self.assertIn("pl", summary["volume_shares_percent"])
        self.assertIn("cs", summary["volume_shares_percent"])
        self.assertIn("Найбільший абсолютний ринок: PL", summary["executive_summary"])


if __name__ == "__main__":
    unittest.main()
