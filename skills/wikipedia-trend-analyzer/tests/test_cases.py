"""
End-to-End Validation Test Suite for Wikipedia Trend Analyzer.

Цей тестовий набір валідує всі бізнес-сценарії кейсу:
1. Фізика: порівняння зростання в німецькомовній (de), польській (pl), україномовній (uk) Вікіпедії за 2 роки.
2. Математика: аналіз німецькомовного розділу (de) з оцінкою надійності тренду (Reliability Score).
3. Християнство: крос-мовне порівняння між 7 мовними розділами (en, de, fr, uk, pl, es, it) з рекомендаціями пріоритетних аудиторій.
4. Інтервальне голодування: порівняння PL vs CS за 2 роки з ручним перевизначенням (override).
5. Астрономія: оцінка надійності попиту в україномовному розділі Вікіпедії.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import pytest

# Add skill directory and scripts/ to sys.path
SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
for p in [str(SKILL_DIR), str(SCRIPTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from run import SkillOrchestrator


@pytest.fixture(scope="module")
def orchestrator() -> SkillOrchestrator:
    return SkillOrchestrator()


class TestE2ECases:
    """E2E тести для перевірки бізнес-сценаріїв та контрактів."""

    def test_case_1_physics_de_pl_uk(self, orchestrator: SkillOrchestrator):
        """
        Сценарій 1: Фізика (de, pl, uk) за 2 роки.
        """
        result = orchestrator.run_multi(
            topic="Фізика",
            langs=["de", "pl", "uk"],
            years=2,
            generate_pdf=True,
            generate_chart=True,
        )

        assert result["status"] == "success"
        assert result["mode"] == "cross_lingual_comparison"
        assert set(result["languages_analyzed"]) == {"de", "pl", "uk"}
        assert len(result["missing_languages"]) == 0

        # Перевірка метрик
        de_metrics = result["languages"]["de"]
        pl_metrics = result["languages"]["pl"]
        uk_metrics = result["languages"]["uk"]

        assert de_metrics["total_views"] > 50_000
        assert pl_metrics["total_views"] > 20_000
        assert uk_metrics["total_views"] > 10_000

        # DE як найбільший ринок
        comp = result["comparison"]
        assert comp["top_market_by_volume"] == "de"
        assert comp["volume_shares_percent"]["de"] > 50.0

        # Перевірка згенерованих артефактів
        assert result["artifacts"]["chart_png"] is not None
        assert os.path.exists(result["artifacts"]["chart_png"])
        assert result["artifacts"]["report_pdf"] is not None
        assert os.path.exists(result["artifacts"]["report_pdf"])

    def test_case_2_mathematics_de_reliability(self, orchestrator: SkillOrchestrator):
        """
        Сценарій 2: Математика (de.wikipedia) з оцінкою надійності тренду.
        """
        result = orchestrator.run_single(
            article="Mathematik",
            project="de.wikipedia",
            years=2,
            generate_pdf=True,
            generate_chart=True,
        )

        assert result["status"] == "success"
        assert result["mode"] == "single_article"
        assert result["lang"] == "de"

        # Метрики обсягу та росту
        m = result["metrics"]
        assert m["total_views"] > 100_000
        assert m["daily_average"] > 100.0

        # Оцінка надійності (Reliability Score)
        rel = result["reliability"]
        assert rel["score"] >= 80.0
        assert rel["confidence_level"] == "High"
        assert "надійність" in rel["verdict"].lower() or "достовірн" in rel["verdict"].lower() or "інтерес" in rel["verdict"].lower()

        # Наявність артефактів
        assert os.path.exists(result["artifacts"]["chart_png"])
        assert os.path.exists(result["artifacts"]["report_pdf"])

    def test_case_3_christianity_7_languages(self, orchestrator: SkillOrchestrator):
        """
        Сценарій 3: Християнство у 7 мовних розділах (en, de, fr, uk, pl, es, it).
        """
        target_langs = ["en", "de", "fr", "uk", "pl", "es", "it"]
        result = orchestrator.run_multi(
            topic="Християнство",
            langs=target_langs,
            years=2,
            generate_pdf=True,
            generate_chart=True,
        )

        assert result["status"] == "success"
        assert set(result["languages_analyzed"]) == set(target_langs)

        # EN — беззаперечний глобальний лідер за обсягом
        assert result["comparison"]["top_market_by_volume"] == "en"
        en_views = result["languages"]["en"]["total_views"]
        assert en_views > 1_000_000

        # ES — другий за обсягом ринок
        es_views = result["languages"]["es"]["total_views"]
        assert es_views > 500_000
        assert es_views > result["languages"]["de"]["total_views"]

        # Усі мови мають високу надійність через великий обсяг
        for lang in target_langs:
            assert result["languages"][lang]["reliability_score"] >= 80.0

        # Перевірка артефактів
        assert os.path.exists(result["artifacts"]["chart_png"])
        assert os.path.exists(result["artifacts"]["report_pdf"])

    def test_case_intermittent_fasting_override(self, orchestrator: SkillOrchestrator):
        """
        Сценарій: Інтервальне голодування (PL vs CS) з ручним перевизначенням.
        """
        result = orchestrator.run_multi(
            topic="інтервальне голодування",
            langs=["pl", "cs"],
            years=2,
            overrides={"pl": "Głodówka_lecznicza"},
            generate_pdf=True,
            generate_chart=True,
        )

        assert result["status"] == "success"
        assert result["languages"]["pl"]["article"] == "Głodówka_lecznicza"
        assert result["languages"]["cs"]["article"] == "Přerušovaný_půst"
        assert result["languages"]["pl"]["total_views"] > 1000
        assert result["languages"]["cs"]["total_views"] > 1000

    def test_case_astronomy_uk(self, orchestrator: SkillOrchestrator):
        """
        Сценарій: Астрономія (uk.wikipedia).
        """
        result = orchestrator.run_single(
            article="Астрономія",
            project="uk.wikipedia",
            years=2,
            generate_pdf=True,
            generate_chart=True,
        )

        assert result["status"] == "success"
        assert result["metrics"]["total_views"] > 5000
        assert result["reliability"]["score"] > 50.0
