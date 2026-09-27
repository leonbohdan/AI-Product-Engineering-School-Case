#!/usr/bin/env python3
"""
Trend Analytics and Reliability Scoring Module for Wikipedia Pageviews.

Цей модуль реалізує алгоритми аналізу трендів згідно з ADR-0003:
1. Розрахунок метрик обсягу (сумарні, середньодобові, медіанні перегляди).
2. Розрахунок динаміки зростання:
   - YoY (Year-over-Year) — порівняння останніх 365 днів з попередніми 365 днями.
   - MoM (Month-over-Month) — порівняння останніх 30 днів з попередніми 30 днями.
   - Органічний YoY — розрахунок на базі медіанного бейзлайну без урахування аномалій.
3. Детекція новинних спалахів (Spike Detection) через IQR ($Q3 + 1.5 \\times \\text{IQR}$)
   та розрахунок Spike Impact Ratio (% переглядів у пікові дні).
4. Розрахунок композитного індексу довіри (Reliability Score, 0–100%)
   з трьома факторами: обсяг (40%), стабільність ряду (30%), органічність (30%).
5. Крос-мовне порівняння ринків (Multi-language comparison).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Додавання поточної директорії до sys.path для коректного імпорту сусідніх модулів
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from topic_resolver import TopicResolver, parse_override_arg
from wikimedia_client import PageViewsResult, WikimediaClient

logger = logging.getLogger("trend_analyzer")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


@dataclass
class SpikeEvent:
    """Одиничний новинний або подієвий спалах."""
    date: str
    views: int
    median: float
    ratio_to_median: float
    threshold: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VolumeMetrics:
    """Метрики обсягу трафіку статті."""
    total_views: int
    days_count: int
    daily_avg: float
    daily_median: float
    daily_max: int
    daily_min: int
    recent_year_views: Optional[int] = None
    prior_year_views: Optional[int] = None
    recent_30d_views: Optional[int] = None
    prior_30d_views: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GrowthMetrics:
    """Метрики динаміки зростання інтересу."""
    yoy_growth_percent: Optional[float]
    yoy_organic_growth_percent: Optional[float]
    mom_growth_percent: Optional[float]
    direction: str  # 'growing', 'stable', 'declining', 'insufficient_data'

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SpikeMetrics:
    """Метрики новинних спалахів та аномалій."""
    q1: float
    q3: float
    iqr: float
    spike_threshold: float
    spike_days_count: int
    spike_views_total: int
    spike_impact_percent: float
    top_spikes: List[SpikeEvent]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "q1": round(self.q1, 2),
            "q3": round(self.q3, 2),
            "iqr": round(self.iqr, 2),
            "spike_threshold": round(self.spike_threshold, 2),
            "spike_days_count": self.spike_days_count,
            "spike_views_total": self.spike_views_total,
            "spike_impact_percent": round(self.spike_impact_percent, 2),
            "top_spikes": [s.to_dict() for s in self.top_spikes],
        }


@dataclass
class ReliabilityBreakdown:
    """Композитна оцінка індексу довіри (Reliability Score 0–100%)."""
    volume_score: float        # 0 - 40 балів
    volume_rationale: str
    stability_score: float     # 0 - 30 балів
    cv: float                  # Коефіцієнт варіації (sigma / mean)
    stability_rationale: str
    organic_score: float       # 0 - 30 балів
    organic_rationale: str
    total_score: float         # 0 - 100 балів
    confidence_level: str      # 'High', 'Moderate', 'Low'
    verdict: str               # Пояснення для фаундерів та інвесторів

    def to_dict(self) -> Dict[str, Any]:
        return {
            "volume_score": round(self.volume_score, 1),
            "volume_rationale": self.volume_rationale,
            "stability_score": round(self.stability_score, 1),
            "cv": round(self.cv, 3),
            "stability_rationale": self.stability_rationale,
            "organic_score": round(self.organic_score, 1),
            "organic_rationale": self.organic_rationale,
            "total_score": round(self.total_score, 1),
            "confidence_level": self.confidence_level,
            "verdict": self.verdict,
        }


@dataclass
class TrendAnalysisResult:
    """Повний результат аналізу тренду для окремої статті/мови."""
    project: str
    lang: str
    article: str
    display_title: str
    start_date: str
    end_date: str
    days_analyzed: int
    volume: VolumeMetrics
    growth: GrowthMetrics
    spikes: SpikeMetrics
    reliability: ReliabilityBreakdown

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project": self.project,
            "lang": self.lang,
            "article": self.article,
            "display_title": self.display_title,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "days_analyzed": self.days_analyzed,
            "volume": self.volume.to_dict(),
            "growth": self.growth.to_dict(),
            "spikes": self.spikes.to_dict(),
            "reliability": self.reliability.to_dict(),
        }


@dataclass
class MultiLanguageComparisonResult:
    """Результат порівняльного аналізу для кількох мовних розділів."""
    topic: str
    base_lang: str
    wikidata_id: Optional[str]
    description: Optional[str]
    results: Dict[str, TrendAnalysisResult]
    missing_languages: List[str]
    comparison_summary: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topic": self.topic,
            "base_lang": self.base_lang,
            "wikidata_id": self.wikidata_id,
            "description": self.description,
            "languages_analyzed": list(self.results.keys()),
            "missing_languages": self.missing_languages,
            "comparison_summary": self.comparison_summary,
            "articles": {lang: res.to_dict() for lang, res in self.results.items()},
        }


class TrendAnalyzer:
    """
    Аналізатор трендів, фільтрації новинних спалахів та розрахунку індексу довіри.
    """

    def __init__(
        self,
        wikimedia_client: Optional[WikimediaClient] = None,
        topic_resolver: Optional[TopicResolver] = None,
    ) -> None:
        self.client = wikimedia_client or WikimediaClient()
        self.resolver = topic_resolver or TopicResolver()

    # -------------------------------------------------------------------------
    # Розрахунок метрик на базі часового ряду (Pandas DataFrame)
    # -------------------------------------------------------------------------

    def analyze_dataframe(
        self,
        df: pd.DataFrame,
        project: str,
        article: str,
        display_title: Optional[str] = None,
    ) -> TrendAnalysisResult:
        """
        Аналізує DataFrame з колонками ['date', 'views'] (або індексом 'date').
        """
        if df.empty:
            raise ValueError(f"DataFrame для {project}/{article} є порожнім.")

        work_df = df.copy()
        if "views" not in work_df.columns:
            raise ValueError("DataFrame має містити колонку 'views'.")

        if not isinstance(work_df.index, pd.DatetimeIndex):
            if "date" in work_df.columns:
                work_df["date"] = pd.to_datetime(work_df["date"])
                work_df = work_df.set_index("date")
            else:
                work_df.index = pd.to_datetime(work_df.index)

        work_df = work_df.sort_index()

        # Заповнення пропусків у часовому ряді нулями
        full_idx = pd.date_range(start=work_df.index.min(), end=work_df.index.max(), freq="D")
        work_df = work_df.reindex(full_idx, fill_value=0)

        views_series = work_df["views"].astype(float)
        days_count = len(views_series)
        total_views = int(views_series.sum())
        daily_avg = float(views_series.mean())
        daily_median = float(views_series.median())
        daily_max = int(views_series.max())
        daily_min = int(views_series.min())

        start_date = work_df.index.min().strftime("%Y-%m-%d")
        end_date = work_df.index.max().strftime("%Y-%m-%d")

        # 1. Детекція спалахів (IQR per ADR-0003)
        spikes_metrics, clean_series = self._detect_spikes(views_series, daily_median)

        # 2. Розрахунок періодів YoY та MoM
        recent_year_views = None
        prior_year_views = None
        recent_30d_views = None
        prior_30d_views = None
        yoy_growth = None
        yoy_organic_growth = None
        mom_growth = None

        if days_count >= 60:
            recent_30d = views_series.iloc[-30:]
            prior_30d = views_series.iloc[-60:-30]
            recent_30d_views = int(recent_30d.sum())
            prior_30d_views = int(prior_30d.sum())
            if prior_30d_views > 0:
                mom_growth = round(((recent_30d_views - prior_30d_views) / prior_30d_views) * 100.0, 2)

        if days_count >= 400:
            # Маємо достатньо даних для 2-річного порівняння (365 днів)
            recent_year = views_series.iloc[-365:]
            prior_year = views_series.iloc[-730:-365] if days_count >= 730 else views_series.iloc[:-365]

            recent_year_views = int(recent_year.sum())
            prior_year_views = int(prior_year.sum())

            if prior_year_views > 0:
                # Звичайний YoY
                yoy_growth = round(((recent_year_views - prior_year_views) / prior_year_views) * 100.0, 2)

                # Органічний YoY (медіанний бейзлайн з очищеного ряду)
                clean_recent = clean_series.iloc[-365:]
                clean_prior = clean_series.iloc[-730:-365] if days_count >= 730 else clean_series.iloc[:-365]
                med_recent = clean_recent.median()
                med_prior = clean_prior.median()
                if med_prior > 0:
                    yoy_organic_growth = round(((med_recent - med_prior) / med_prior) * 100.0, 2)

        # Визначення тренду
        growth_val = yoy_growth if yoy_growth is not None else mom_growth
        if growth_val is None:
            direction = "insufficient_data"
        elif growth_val >= 10.0:
            direction = "growing"
        elif growth_val <= -10.0:
            direction = "declining"
        else:
            direction = "stable"

        volume_metrics = VolumeMetrics(
            total_views=total_views,
            days_count=days_count,
            daily_avg=round(daily_avg, 2),
            daily_median=round(daily_median, 2),
            daily_max=daily_max,
            daily_min=daily_min,
            recent_year_views=recent_year_views,
            prior_year_views=prior_year_views,
            recent_30d_views=recent_30d_views,
            prior_30d_views=prior_30d_views,
        )

        growth_metrics = GrowthMetrics(
            yoy_growth_percent=yoy_growth,
            yoy_organic_growth_percent=yoy_organic_growth,
            mom_growth_percent=mom_growth,
            direction=direction,
        )

        # 3. Розрахунок Reliability Score (ADR-0003)
        reliability = self._calculate_reliability_score(
            daily_avg=daily_avg,
            clean_series=clean_series,
            spike_impact=spikes_metrics.spike_impact_percent,
            growth_val=growth_val,
        )

        lang = project.split(".")[0].lower() if "." in project else project.lower()

        return TrendAnalysisResult(
            project=project,
            lang=lang,
            article=article,
            display_title=display_title or article.replace("_", " "),
            start_date=start_date,
            end_date=end_date,
            days_analyzed=days_count,
            volume=volume_metrics,
            growth=growth_metrics,
            spikes=spikes_metrics,
            reliability=reliability,
        )

    # -------------------------------------------------------------------------
    # Детекція спалахів (IQR)
    # -------------------------------------------------------------------------

    def _detect_spikes(
        self, views_series: pd.Series, median_val: float
    ) -> Tuple[SpikeMetrics, pd.Series]:
        """
        Виявляє дні зі спалахами через IQR (Threshold = Median + 3 * IQR).
        Повертає SpikeMetrics та очищений ряд (де спалахи замінені на поріг).
        """
        q1 = float(views_series.quantile(0.25))
        q3 = float(views_series.quantile(0.75))
        iqr = q3 - q1

        # Поріг відсікання новинного хайпу згідно з ADR-0003:
        # Views > Median + 3 * IQR (або Q3 + 1.5 * IQR, якщо IQR дуже малий)
        threshold = max(median_val + 3.0 * iqr, q3 + 1.5 * iqr, 10.0)

        spike_mask = views_series > threshold
        spike_days_count = int(spike_mask.sum())
        spike_views_total = int(views_series[spike_mask].sum())
        total_views = int(views_series.sum())

        spike_impact = (spike_views_total / total_views * 100.0) if total_views > 0 else 0.0

        top_spikes: List[SpikeEvent] = []
        if spike_days_count > 0:
            spikes_df = views_series[spike_mask].sort_values(ascending=False).head(5)
            for dt, val in spikes_df.items():
                date_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)
                ratio = round(val / median_val, 1) if median_val > 0 else round(val, 1)
                top_spikes.append(
                    SpikeEvent(
                        date=date_str,
                        views=int(val),
                        median=round(median_val, 1),
                        ratio_to_median=ratio,
                        threshold=round(threshold, 1),
                    )
                )

        # Очищений ряд для розрахунку органічного тренду (без екстремальних піків)
        clean_series = views_series.copy()
        clean_series[spike_mask] = threshold

        metrics = SpikeMetrics(
            q1=q1,
            q3=q3,
            iqr=iqr,
            spike_threshold=threshold,
            spike_days_count=spike_days_count,
            spike_views_total=spike_views_total,
            spike_impact_percent=spike_impact,
            top_spikes=top_spikes,
        )
        return metrics, clean_series

    # -------------------------------------------------------------------------
    # Розрахунок Reliability Score (ADR-0003)
    # -------------------------------------------------------------------------

    def _calculate_reliability_score(
        self,
        daily_avg: float,
        clean_series: pd.Series,
        spike_impact: float,
        growth_val: Optional[float],
    ) -> ReliabilityBreakdown:
        """
        Розраховує оцінку надійності тренду (0–100 балів) за трьома компонентами:
        1. Обсяг (40 балів)
        2. Стабільність (30 балів)
        3. Органічність / відсутність спайків (30 балів)
        """
        # 1. Volume Score (max 40)
        if daily_avg >= 500:
            vol_score = 40.0
            vol_msg = f"Високий обсяг трафіку ({daily_avg:.1f} переглядів/день > 500), висока репрезентативність."
        elif daily_avg >= 100:
            # Лінійна шкала 30 - 40
            vol_score = 30.0 + 10.0 * ((daily_avg - 100.0) / 400.0)
            vol_msg = f"Достатній обсяг трафіку ({daily_avg:.1f} переглядів/день), низький ризик шуму."
        elif daily_avg >= 30:
            # Лінійна шкала 15 - 30
            vol_score = 15.0 + 15.0 * ((daily_avg - 30.0) / 70.0)
            vol_msg = f"Помірний обсяг трафіку ({daily_avg:.1f} переглядів/день), можливий статистичний шум."
        elif daily_avg >= 5:
            # Лінійна шкала 5 - 15
            vol_score = 5.0 + 10.0 * ((daily_avg - 5.0) / 25.0)
            vol_msg = f"Малий обсяг трафіку ({daily_avg:.1f} переглядів/день), висока вразливість до шуму малих чисел."
        else:
            vol_score = max(1.0, daily_avg)
            vol_msg = f"Критично низький обсяг ({daily_avg:.1f} переглядів/день). Дані статистично ненадійні."

        # 2. Stability Score (max 30) через коефіцієнт варіації 30-денного згладженого ряду
        rolling_30 = clean_series.rolling(window=14, min_periods=7).mean().dropna()
        mean_30 = rolling_30.mean()
        std_30 = rolling_30.std()
        cv = (std_30 / mean_30) if mean_30 > 0 else 1.0

        if cv <= 0.25:
            stab_score = 30.0
            stab_msg = f"Дуже висока стабільність часового ряду (CV = {cv:.2f} <= 0.25)."
        elif cv <= 0.50:
            stab_score = 25.0 + 5.0 * ((0.50 - cv) / 0.25)
            stab_msg = f"Добра стабільність інтересу (CV = {cv:.2f})."
        elif cv <= 0.80:
            stab_score = 18.0 + 7.0 * ((0.80 - cv) / 0.30)
            stab_msg = f"Помірна циклічність або сезонність (CV = {cv:.2f})."
        elif cv <= 1.20:
            stab_score = 10.0 + 8.0 * ((1.20 - cv) / 0.40)
            stab_msg = f"Висока волатильність і різкі стрибки уваги (CV = {cv:.2f})."
        else:
            stab_score = max(2.0, 10.0 - (cv - 1.2) * 5.0)
            stab_msg = f"Хаотичний часовий ряд без стабільного рівня (CV = {cv:.2f})."

        # 3. Organic Score (max 30) через штраф за новинні спалахи
        if spike_impact < 5.0:
            org_score = 30.0
            org_msg = f"Органічний інтерес: спалахи складають лише {spike_impact:.1f}% трафіку."
        elif spike_impact < 15.0:
            org_score = 25.0 + 5.0 * ((15.0 - spike_impact) / 10.0)
            org_msg = f"Здоровий баланс: новинні сплески формують {spike_impact:.1f}% переглядів."
        elif spike_impact < 25.0:
            org_score = 18.0 + 7.0 * ((25.0 - spike_impact) / 10.0)
            org_msg = f"Помітний новинний шум: {spike_impact:.1f}% переглядів припадають на пікові дні."
        elif spike_impact < 35.0:
            org_score = 10.0 + 8.0 * ((35.0 - spike_impact) / 10.0)
            org_msg = f"Високий хайповий вплив: {spike_impact:.1f}% переглядів припадають на короткочасні спалахи."
        else:
            org_score = max(1.0, 10.0 - (spike_impact - 35.0) * 0.5)
            org_msg = f"Критичний новинний шум ({spike_impact:.1f}% трафіку). Тренд сформовано одиничними подіями."

        total = round(vol_score + stab_score + org_score, 1)
        total = min(max(total, 0.0), 100.0)

        if total >= 80.0:
            conf_level = "High"
            verdict = (
                f"Стійкий органічний ринковий інтерес (надійність {total:.0f}/100, High Confidence). "
                f"Трафік достатній ({daily_avg:.0f} переглядів/день) і стабільний. Можна спиратися для валідації попиту."
            )
        elif total >= 50.0:
            conf_level = "Moderate"
            verdict = (
                f"Помірний рівень надійності тренду ({total:.0f}/100, Moderate Confidence). "
                f"Сигнал є позитивним, але потребує валідації зовнішніми джерелами (Google Trends, App Store)."
            )
        else:
            conf_level = "Low"
            verdict = (
                f"Низький рівень надійності тренду ({total:.0f}/100, Low Confidence). "
                f"Показники обумовлені {vol_msg.lower()} або значним впливом спайків. Довіряти як бізнес-сигналу ризиковано."
            )

        return ReliabilityBreakdown(
            volume_score=vol_score,
            volume_rationale=vol_msg,
            stability_score=stab_score,
            cv=cv,
            stability_rationale=stab_msg,
            organic_score=org_score,
            organic_rationale=org_msg,
            total_score=total,
            confidence_level=conf_level,
            verdict=verdict,
        )

    # -------------------------------------------------------------------------
    # Публічні методи аналізу
    # -------------------------------------------------------------------------

    def analyze_article(
        self,
        project: str,
        article: str,
        years: int = 2,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> TrendAnalysisResult:
        """
        Завантажує дані через WikimediaClient та повертає повний TrendAnalysisResult.
        """
        pageviews = self.client.get_article_pageviews(
            project=project,
            article=article,
            years=years,
            start=start,
            end=end,
        )
        df = pageviews.to_dataframe()
        return self.analyze_dataframe(
            df=df,
            project=pageviews.project,
            article=pageviews.article,
        )

    def analyze_topic(
        self,
        topic: str,
        langs: List[str],
        years: int = 2,
        base_lang: Optional[str] = None,
        overrides: Optional[Dict[str, str]] = None,
    ) -> MultiLanguageComparisonResult:
        """
        Повний наскрізний цикл:
        1. Резолв сутності та мов через TopicResolver.
        2. Завантаження даних для кожної знайденої мови через WikimediaClient.
        3. Розрахунок метрик та Reliability Score для кожної мови.
        4. Компаративний синтез та формування рекомендацій.
        """
        resolved = self.resolver.resolve(
            topic=topic,
            target_langs=langs,
            base_lang=base_lang,
            overrides=overrides,
        )

        results: Dict[str, TrendAnalysisResult] = {}
        missing_langs: List[str] = []

        for lang, art in resolved.articles.items():
            if not art.found or not art.title:
                missing_langs.append(lang)
                continue

            try:
                res = self.analyze_article(
                    project=art.project,
                    article=art.title,
                    years=years,
                )
                results[lang] = res
            except Exception as e:
                logger.warning(f"Не вдалося завантажити/проаналізувати дані для {art.project}/{art.title}: {e}")
                missing_langs.append(lang)

        comparison_summary = self._synthesize_comparison(topic, results, missing_langs)

        return MultiLanguageComparisonResult(
            topic=topic,
            base_lang=resolved.base_lang,
            wikidata_id=resolved.wikidata_id,
            description=resolved.description,
            results=results,
            missing_languages=missing_langs,
            comparison_summary=comparison_summary,
        )

    def _synthesize_comparison(
        self,
        topic: str,
        results: Dict[str, TrendAnalysisResult],
        missing_langs: List[str],
    ) -> Dict[str, Any]:
        """
        Синтезує бізнес-порівняння між різними мовними розділами.
        """
        if not results:
            return {
                "verdict": "Жодного мовного розділу не вдалося проаналізувати.",
                "rankings": [],
            }

        # Рейтинг за обсягом переглядів
        sorted_by_volume = sorted(
            results.items(), key=lambda item: item[1].volume.total_views, reverse=True
        )

        # Рейтинг за темпом зростання (YoY або MoM)
        sorted_by_growth = sorted(
            results.items(),
            key=lambda item: (item[1].growth.yoy_growth_percent or -999.0),
            reverse=True,
        )

        # Рейтинг за індексом довіри
        sorted_by_reliability = sorted(
            results.items(),
            key=lambda item: item[1].reliability.total_score,
            reverse=True,
        )

        top_volume_lang, top_volume_res = sorted_by_volume[0]
        top_growth_lang, top_growth_res = sorted_by_growth[0]
        top_rel_lang, top_rel_res = sorted_by_reliability[0]

        # Розрахунок відносних пропорцій обсягу
        volume_shares: Dict[str, float] = {}
        total_all_views = sum(r.volume.total_views for r in results.values())
        for l, r in results.items():
            share = (r.volume.total_views / total_all_views * 100.0) if total_all_views > 0 else 0.0
            volume_shares[l] = round(share, 1)

        summary_text = (
            f"Найбільший абсолютний ринок: {top_volume_lang.upper()} ({top_volume_res.volume.total_views:,} переглядів, "
            f"{volume_shares.get(top_volume_lang)}% від сукупного попиту). "
        )

        if top_growth_res.growth.yoy_growth_percent is not None and top_growth_res.growth.yoy_growth_percent > 0:
            summary_text += (
                f"Найшвидше зростання демонструє {top_growth_lang.upper()} "
                f"(+{top_growth_res.growth.yoy_growth_percent:.1f}% YoY). "
            )

        summary_text += (
            f"Найвища достовірність тренду: {top_rel_lang.upper()} "
            f"({top_rel_res.reliability.total_score:.0f}/100, {top_rel_res.reliability.confidence_level})."
        )

        if missing_langs:
            summary_text += (
                f" ⚠️ Зверніть увагу: для мов [{', '.join(l.upper() for l in missing_langs)}] статті відсутні "
                "або мають специфічну термінологію, що свідчить про слабку представленість ринку."
            )

        return {
            "top_market_by_volume": top_volume_lang,
            "top_market_by_growth": top_growth_lang,
            "top_market_by_reliability": top_rel_lang,
            "volume_shares_percent": volume_shares,
            "total_combined_views": total_all_views,
            "executive_summary": summary_text,
        }


# -----------------------------------------------------------------------------
# CLI Інтерфейс
# -----------------------------------------------------------------------------

def main() -> None:
    """Точка входу для CLI виклику аналізатора трендів."""
    parser = argparse.ArgumentParser(
        description="Wikipedia Trend Analytics & Reliability Scoring CLI (ADR-0003)"
    )
    parser.add_argument("--topic", "-t", help="Тема для аналізу (наприклад 'інтервальне голодування', 'астрономія')")
    parser.add_argument("--article", "-a", help="Точна назва статті (якщо досліджується одна конкретна стаття)")
    parser.add_argument("--project", "-p", default="uk.wikipedia", help="Проєкт Вікіпедії (за замовчуванням uk.wikipedia)")
    parser.add_argument("--langs", "-l", help="Список мов через кому для крос-мовного аналізу (наприклад 'pl,cs,uk')")
    parser.add_argument("--years", "-y", type=int, default=2, help="Кількість років аналізу (за замовчуванням 2)")
    parser.add_argument("--override", "-o", help="Ручні відповідності виду 'pl:Głodówka_lecznicza'")
    parser.add_argument("--json", action="store_true", help="Вивести чистий структурований JSON")

    args = parser.parse_args()

    analyzer = TrendAnalyzer()

    # 1. Сценарій: Крос-мовний аналіз теми для декількох мов
    if args.langs and (args.topic or args.article):
        topic_name = args.topic or args.article
        target_langs = [l.strip() for l in args.langs.split(",") if l.strip()]
        overrides = parse_override_arg(args.override)

        multi_res = analyzer.analyze_topic(
            topic=topic_name,
            langs=target_langs,
            years=args.years,
            overrides=overrides,
        )

        if args.json:
            print(json.dumps(multi_res.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"\n📈 Крос-мовний тренд-аналіз для теми: «{multi_res.topic}» ({args.years} роки)")
            if multi_res.wikidata_id:
                print(f"Wikidata ID: {multi_res.wikidata_id} ({multi_res.description or ''})")
            print("=" * 105)
            print(
                f"{'Мова':<6} | {'Стаття':<28} | {'Перегляди':<11} | {'YoY ріст':<10} | {'Spike %':<8} | {'Довіра':<7} | {'Рівень'}"
            )
            print("-" * 105)

            for lang, res in multi_res.results.items():
                yoy_str = f"{res.growth.yoy_growth_percent:+.1f}%" if res.growth.yoy_growth_percent is not None else "N/A"
                print(
                    f"{lang.upper():<6} | "
                    f"{res.display_title[:28]:<28} | "
                    f"{res.volume.total_views:>11,} | "
                    f"{yoy_str:>10} | "
                    f"{res.spikes.spike_impact_percent:>7.1f}% | "
                    f"{res.reliability.total_score:>6.0f}/100 | "
                    f"{res.reliability.confidence_level}"
                )

            if multi_res.missing_languages:
                for ml in multi_res.missing_languages:
                    print(f"{ml.upper():<6} | {'(стаття відсутня / не знайдена)':<28} | {'—':>11} | {'—':>10} | {'—':>8} | {'0/100':>7} | N/A")

            print("=" * 105)
            print(f"\n💡 Підсумок для керівництва:")
            print(multi_res.comparison_summary.get("executive_summary", ""))
            print()

        return

    # 2. Сценарій: Аналіз однієї статті
    article_name = args.article or args.topic
    if not article_name:
        parser.error("Необхідно вказати --article або --topic.")

    res = analyzer.analyze_article(
        project=args.project,
        article=article_name,
        years=args.years,
    )

    if args.json:
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"\n📊 Аналітика тренду та надійності: {res.display_title} ({res.project})")
        print(f"Період: {res.start_date} — {res.end_date} ({res.days_analyzed} днів)")
        print("=" * 80)
        print("📌 МЕТРИКИ ОБСЯГУ ТА ДИНАМІКИ:")
        print(f"  • Сумарні перегляди:       {res.volume.total_views:,}")
        print(f"  • Середньодобові:          {res.volume.daily_avg:,.1f} (медіана: {res.volume.daily_median:,.1f})")
        if res.growth.yoy_growth_percent is not None:
            print(f"  • Зростання YoY:           {res.growth.yoy_growth_percent:+.2f}% (останні 365д vs попередні 365д)")
        if res.growth.yoy_organic_growth_percent is not None:
            print(f"  • Органічний YoY:          {res.growth.yoy_organic_growth_percent:+.2f}% (медіанний бейзлайн)")
        if res.growth.mom_growth_percent is not None:
            print(f"  • Зростання MoM:           {res.growth.mom_growth_percent:+.2f}% (останні 30д vs попередні 30д)")
        print(f"  • Напрямок тренду:         {res.growth.direction}")

        print("\n⚡ ДЕТЕКЦІЯ НОВИННИХ СПАЛАХІВ (Spike Detection):")
        print(f"  • Поріг спалаху (IQR):     {res.spikes.spike_threshold:,.1f} переглядів/день")
        print(f"  • Кількість пікових днів:  {res.spikes.spike_days_count} днів ({res.spikes.spike_views_total:,} переглядів)")
        print(f"  • Spike Impact Ratio:      {res.spikes.spike_impact_percent:.1f}% від усіх переглядів")
        if res.spikes.top_spikes:
            print("  • Топ аномальні дні:")
            for s in res.spikes.top_spikes[:3]:
                print(f"      - {s.date}: {s.views:,} переглядів ({s.ratio_to_median}x від медіани)")

        print("\n🎯 ІНДЕКС ДОВІРИ (Reliability Score):")
        print(f"  • Підсумковий бал:         {res.reliability.total_score:.0f} / 100 ({res.reliability.confidence_level} Confidence)")
        print(f"  • Фактор обсягу (40%):     {res.reliability.volume_score:.1f} / 40 ({res.reliability.volume_rationale})")
        print(f"  • Фактор стабільності(30%):{res.reliability.stability_score:.1f} / 30 ({res.reliability.stability_rationale})")
        print(f"  • Фактор органики (30%):   {res.reliability.organic_score:.1f} / 30 ({res.reliability.organic_rationale})")
        print(f"\n💡 Вердикт для фаундера:")
        print(f"  {res.reliability.verdict}\n")


if __name__ == "__main__":
    main()
