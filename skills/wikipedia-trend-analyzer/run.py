#!/usr/bin/env python3
"""
Main CLI Orchestrator for Wikipedia Trend Analyzer Agent Skill.

Цей модуль є єдиною точкою входу (orchestrator) для виклику AI-агентом або користувачем:
1. Приймає параметри запиту (--topic, --langs, --years, --pdf, --json тощо).
2. Автоматично координує модулі:
   - TopicResolver (резолв назв та сутностей через Wikidata).
   - WikimediaClient (отримання даних з обов'язковою фільтрацією ботів agent=user).
   - TrendAnalyzer (YoY, MoM, детекція новинних сплесків через IQR, Reliability Score).
   - ChartGenerator (генерація наочних графіків).
   - PDFReportGenerator (компіляція строго 1-сторінкового A4 звіту).
3. Повертає компактний структурований JSON (економія контексту для Claude Haiku 4.5).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Додавання scripts/ до sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from chart_generator import ChartGenerator
from pdf_generator import PDFReportGenerator
from topic_resolver import TopicResolver, parse_override_arg
from trend_analyzer import (
    MultiLanguageComparisonResult,
    TrendAnalysisResult,
    TrendAnalyzer,
)
from wikimedia_client import WikimediaClient

# Логування в stderr, щоб stdout залишався чистим для JSON-контракту агента
logger = logging.getLogger("orchestrator")
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class SkillOrchestrator:
    """
    Головний координатор навички аналізу трендів Вікіпедії.
    """

    def __init__(self) -> None:
        self.client = WikimediaClient()
        self.resolver = TopicResolver()
        self.analyzer = TrendAnalyzer(wikimedia_client=self.client, topic_resolver=self.resolver)
        self.chart_gen = ChartGenerator()
        self.pdf_gen = PDFReportGenerator()

    def run_single(
        self,
        article: str,
        project: str = "uk.wikipedia",
        years: int = 2,
        generate_pdf: bool = True,
        generate_chart: bool = True,
    ) -> Dict[str, Any]:
        """
        Аналізує одну конкретну статтю.
        """
        project = WikimediaClient.normalize_project(project)
        analysis = self.analyzer.analyze_article(project=project, article=article, years=years)

        df = analysis.df if analysis.df is not None else self.client.get_article_pageviews(project=project, article=article, years=years).to_dataframe()

        chart_path_str: Optional[str] = None
        pdf_path_str: Optional[str] = None

        if generate_chart or generate_pdf:
            chart_file = self.chart_gen.generate_single_article_chart(analysis, df)
            if generate_chart:
                chart_path_str = str(chart_file)

            if generate_pdf:
                pdf_file = self.pdf_gen.generate_single_report(analysis, chart_file)
                pdf_path_str = str(pdf_file)

        return self._format_single_response(analysis, chart_path_str, pdf_path_str)

    def run_multi(
        self,
        topic: str,
        langs: List[str],
        years: int = 2,
        base_lang: Optional[str] = None,
        overrides: Optional[Dict[str, str]] = None,
        generate_pdf: bool = True,
        generate_chart: bool = True,
    ) -> Dict[str, Any]:
        """
        Аналізує тему для кількох мовних розділів (крос-мовне дослідження).
        """
        multi_res = self.analyzer.analyze_topic(
            topic=topic,
            langs=langs,
            years=years,
            base_lang=base_lang,
            overrides=overrides,
        )

        dfs: Dict[str, Any] = {
            lang: res.df for lang, res in multi_res.results.items() if res.df is not None
        }
        for lang, res in multi_res.results.items():
            if lang not in dfs:
                try:
                    pv = self.client.get_article_pageviews(res.project, res.article, years=years)
                    dfs[lang] = pv.to_dataframe()
                except Exception as e:
                    logger.warning(f"Не вдалося завантажити DataFrame для {lang}: {e}")

        chart_path_str: Optional[str] = None
        pdf_path_str: Optional[str] = None

        if (generate_chart or generate_pdf) and dfs:
            chart_file = self.chart_gen.generate_multi_language_chart(multi_res, dfs)
            if generate_chart:
                chart_path_str = str(chart_file)

            if generate_pdf:
                pdf_file = self.pdf_gen.generate_multi_report(multi_res, chart_file)
                pdf_path_str = str(pdf_file)

        return self._format_multi_response(multi_res, chart_path_str, pdf_path_str)

    def _format_single_response(
        self,
        analysis: TrendAnalysisResult,
        chart_path: Optional[str],
        pdf_path: Optional[str],
    ) -> Dict[str, Any]:
        """Формує компактний JSON-контракт для однієї статті."""
        yoy_val = analysis.growth.yoy_growth_percent
        mom_val = analysis.growth.mom_growth_percent

        return {
            "status": "success",
            "mode": "single_article",
            "topic": analysis.display_title,
            "project": analysis.project,
            "lang": analysis.lang,
            "period": {
                "start": analysis.start_date,
                "end": analysis.end_date,
                "days_analyzed": analysis.days_analyzed,
            },
            "metrics": {
                "total_views": analysis.volume.total_views,
                "daily_average": analysis.volume.daily_avg,
                "daily_median": analysis.volume.daily_median,
                "yoy_growth_percent": yoy_val,
                "organic_yoy_percent": analysis.growth.yoy_organic_growth_percent,
                "mom_growth_percent": mom_val,
                "trend_direction": analysis.growth.direction,
            },
            "spikes": {
                "spike_threshold": analysis.spikes.spike_threshold,
                "spike_days_count": analysis.spikes.spike_days_count,
                "spike_impact_percent": analysis.spikes.spike_impact_percent,
                "top_spike_dates": [s.date for s in analysis.spikes.top_spikes[:3]],
            },
            "reliability": {
                "score": analysis.reliability.total_score,
                "confidence_level": analysis.reliability.confidence_level,
                "volume_score_40": analysis.reliability.volume_score,
                "stability_score_30": analysis.reliability.stability_score,
                "organic_score_30": analysis.reliability.organic_score,
                "verdict": analysis.reliability.verdict,
            },
            "artifacts": {
                "chart_png": chart_path,
                "report_pdf": pdf_path,
            },
            "recommendations": [
                analysis.reliability.verdict,
                "Для ухвалення фінального інвестиційного рішення тріангулюйте ці дані з Google Search Volume та платним Smoke Test попиту.",
            ],
        }

    def _format_multi_response(
        self,
        multi_res: MultiLanguageComparisonResult,
        chart_path: Optional[str],
        pdf_path: Optional[str],
    ) -> Dict[str, Any]:
        """Формує компактний JSON-контракт для мультимовного аналізу."""
        lang_data: Dict[str, Any] = {}
        for lang, res in multi_res.results.items():
            lang_data[lang] = {
                "article": res.article,
                "display_title": res.display_title,
                "total_views": res.volume.total_views,
                "daily_avg": round(res.volume.daily_avg, 1),
                "yoy_growth_percent": res.growth.yoy_growth_percent,
                "spike_impact_percent": round(res.spikes.spike_impact_percent, 1),
                "reliability_score": round(res.reliability.total_score, 1),
                "confidence_level": res.reliability.confidence_level,
            }

        first_res = next(iter(multi_res.results.values())) if multi_res.results else None
        period_data = {
            "start": first_res.start_date if first_res else None,
            "end": first_res.end_date if first_res else None,
            "days_analyzed": first_res.days_analyzed if first_res else None,
        }

        return {
            "status": "success",
            "mode": "cross_lingual_comparison",
            "topic": multi_res.topic,
            "wikidata_id": multi_res.wikidata_id,
            "wikidata_description": multi_res.description,
            "period": period_data,
            "languages_analyzed": list(multi_res.results.keys()),
            "missing_languages": multi_res.missing_languages,
            "comparison": multi_res.comparison_summary,
            "languages": lang_data,
            "artifacts": {
                "chart_png": chart_path,
                "report_pdf": pdf_path,
            },
            "executive_summary": multi_res.comparison_summary.get("executive_summary", ""),
        }


def main() -> None:
    """CLI точка входу."""
    parser = argparse.ArgumentParser(
        description="Wikipedia Trend Analysis Agent Skill Orchestrator (CLI Entrypoint)"
    )
    parser.add_argument("--topic", "-t", help="Тема для дослідження (наприклад 'інтервальне голодування', 'астрономія')")
    parser.add_argument("--article", "-a", help="Точна назва статті у Вікіпедії")
    parser.add_argument("--project", "-p", default="uk.wikipedia", help="Проєкт Вікіпедії (за замовчуванням uk.wikipedia)")
    parser.add_argument("--langs", "-l", help="Список мов через кому для крос-мовного аналізу (наприклад 'pl,cs' або 'uk,pl,cs,de')")
    parser.add_argument("--years", "-y", type=int, default=2, help="Кількість років аналізу (за замовчуванням 2)")
    parser.add_argument("--override", "-o", help="Ручні відповідності статей виду 'pl:Głodówka_lecznicza'")
    parser.add_argument("--pdf", action="store_true", default=True, help="Згенерувати 1-сторінковий PDF-звіт (за замовчуванням увімкнено)")
    parser.add_argument("--no-pdf", dest="pdf", action="store_false", help="Вимкнути генерацію PDF")
    parser.add_argument("--chart", action="store_true", default=True, help="Згенерувати графік PNG")
    parser.add_argument("--no-chart", dest="chart", action="store_false", help="Вимкнути генерацію графіка")
    parser.add_argument("--json", action="store_true", help="Вивести виключно структурований JSON на stdout")

    args = parser.parse_args()

    orchestrator = SkillOrchestrator()

    try:
        # Мультимовний сценарій
        if args.langs and (args.topic or args.article):
            topic_str = args.topic or args.article
            target_langs = [l.strip() for l in args.langs.split(",") if l.strip()]
            overrides = parse_override_arg(args.override)

            result = orchestrator.run_multi(
                topic=topic_str,
                langs=target_langs,
                years=args.years,
                overrides=overrides,
                generate_pdf=args.pdf,
                generate_chart=args.chart,
            )

        # Одномовний сценарій
        elif args.article or args.topic:
            art_str = args.article or args.topic
            result = orchestrator.run_single(
                article=art_str,
                project=args.project,
                years=args.years,
                generate_pdf=args.pdf,
                generate_chart=args.chart,
            )
        else:
            parser.error("Необхідно передати --topic або --article.")

        # Вивід результатів
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("\n" + "=" * 95)
            print("🚀 WIKIPEDIA TREND ANALYZER // ВИКОНАВЧИЙ ЗВІТ")
            print("=" * 95)

            if result["mode"] == "single_article":
                m = result["metrics"]
                r = result["reliability"]
                s = result["spikes"]
                yoy_s = f"{m['yoy_growth_percent']:+.1f}%" if m['yoy_growth_percent'] is not None else "N/A"

                print(f"Тема дослідження:   «{result['topic']}» ({result['project']})")
                print(f"Аналізований період: {result['period']['start']} — {result['period']['end']} ({result['period']['days_analyzed']} днів)")
                print("-" * 95)
                print(f"Сумарні перегляди:   {m['total_views']:,} (в середньому {m['daily_average']:.1f}/день, медіана: {m['daily_median']:.1f})")
                print(f"Динаміка зростання:  YoY: {yoy_s} | Органічний YoY: {m['organic_yoy_percent'] or 'N/A'}% | Тренд: {m['trend_direction']}")
                print(f"Новинні спалахи:     {s['spike_days_count']} пікових днів ({s['spike_impact_percent']:.1f}% трафіку)")
                print(f"Індекс довіри:       {r['score']:.0f}/100 [{r['confidence_level']} Confidence]")
                print(f"Вердикт:             {r['verdict']}")
            else:
                print(f"Тема дослідження:   «{result['topic']}» (Wikidata: {result.get('wikidata_id') or 'N/A'})")
                print(f"Опис сутності:       {result.get('wikidata_description') or '—'}")
                print("-" * 95)
                print(f"{'Мова':<6} | {'Стаття':<26} | {'Перегляди':<12} | {'YoY ріст':<10} | {'Spike %':<8} | {'Довіра':<7} | {'Рівень'}")
                print("-" * 95)
                for l, d in result["languages"].items():
                    yoy_s = f"{d['yoy_growth_percent']:+.1f}%" if d['yoy_growth_percent'] is not None else "N/A"
                    print(
                        f"{l.upper():<6} | "
                        f"{d['display_title'][:26]:<26} | "
                        f"{d['total_views']:>12,} | "
                        f"{yoy_s:>10} | "
                        f"{d['spike_impact_percent']:>7.1f}% | "
                        f"{d['reliability_score']:>6.0f}/100 | "
                        f"{d['confidence_level']}"
                    )
                if result.get("missing_languages"):
                    for ml in result["missing_languages"]:
                        print(f"{ml.upper():<6} | {'(стаття відсутня / не знайдена)':<26} | {'—':>12} | {'—':>10} | {'—':>8} | {'0/100':>7} | N/A")
                print("-" * 95)
                print(f"💡 Виконавче резюме: {result['executive_summary']}")

            print("\n📁 Згенеровані артефакти:")
            if result["artifacts"]["chart_png"]:
                print(f"  • Графік (PNG): {result['artifacts']['chart_png']}")
            if result["artifacts"]["report_pdf"]:
                print(f"  • Звіт (PDF):   {result['artifacts']['report_pdf']}")
            print("=" * 95 + "\n")

    except Exception as e:
        logger.error(f"Помилка виконання навички: {e}")
        if args.json:
            print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False, indent=2))
        else:
            print(f"\n❌ Помилка: {e}\n", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
