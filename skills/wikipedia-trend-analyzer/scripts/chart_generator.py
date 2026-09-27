#!/usr/bin/env python3
"""
Chart Generation Module for Wikipedia Pageviews Trends.

Цей модуль генерує візуалізації згідно з ADR-0004:
1. Одномовний графік: детальна динаміка з 30-денною ковзною середньою,
   підсвічуванням новинних спалахів (IQR), порогом відсікання та KPI-бейджами.
2. Багатомовний графік: порівняння динаміки переглядів між різними мовними
   розділами (UK, PL, CS, EN тощо) з нормалізацією та кольоровим кодуванням.
3. Оптимізація під вбудовування в односторінковий PDF (чистий стиль, чіткі шрифти).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")  # Безголовий режим без GUI
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from topic_resolver import TopicResolver, parse_override_arg
from trend_analyzer import (
    MultiLanguageComparisonResult,
    TrendAnalysisResult,
    TrendAnalyzer,
)
from wikimedia_client import WikimediaClient

logger = logging.getLogger("chart_generator")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "output" / "charts"

# Палітра кольорів для мов
LANG_COLORS: Dict[str, str] = {
    "uk": "#0288d1",  # Deep Sky Blue
    "pl": "#d32f2f",  # Polish Red / Crimson
    "cs": "#388e3c",  # Czech Forest Green
    "en": "#7b1fa2",  # Purple
    "de": "#f57c00",  # Dark Orange
    "es": "#0097a7",  # Cyan / Teal
    "fr": "#c2185b",  # Rose
    "it": "#5d4037",  # Brown
}
FALLBACK_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]


class ChartGenerator:
    """
    Генератор графіків трендів переглядів Вікіпедії.
    """

    def __init__(self, output_dir: Optional[Path] = None) -> None:
        self.output_dir = output_dir or DEFAULT_OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Налаштування стилів Matplotlib
        plt.rcParams.update({
            "font.family": "sans-serif",
            "font.sans-serif": ["DejaVu Sans", "Liberation Sans", "Helvetica", "Arial"],
            "axes.edgecolor": "#cccccc",
            "axes.linewidth": 0.8,
            "grid.color": "#e5e5e5",
            "grid.linestyle": "--",
            "grid.linewidth": 0.6,
        })

    def generate_single_article_chart(
        self,
        analysis: TrendAnalysisResult,
        df: pd.DataFrame,
        output_path: Optional[Path] = None,
        dpi: int = 200,
    ) -> Path:
        """
        Генерує детальний графік для однієї статті з MA30, спайками та KPI.
        """
        if output_path is None:
            clean_proj = analysis.project.replace(".org", "")
            clean_art = analysis.article.replace("/", "_").replace(" ", "_")
            output_path = self.output_dir / f"{clean_proj}_{clean_art}_trend.png"

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

        # Розрахунок ковзних середніх
        work_df["ma30"] = work_df["views"].rolling(window=30, min_periods=7).mean()
        work_df["ma7"] = work_df["views"].rolling(window=7, min_periods=3).mean()

        fig, ax = plt.subplots(figsize=(10, 4.8), dpi=dpi)

        # 1. Сирі щоденні точки / слабка лінія
        ax.plot(
            work_df.index,
            work_df["views"],
            color="#90caf9",
            alpha=0.45,
            linewidth=1.0,
            label="Щоденні перегляди",
            zorder=2,
        )

        # 2. 30-денне згладжування (основний тренд)
        lang_color = LANG_COLORS.get(analysis.lang, "#1565c0")
        ax.plot(
            work_df.index,
            work_df["ma30"],
            color=lang_color,
            linewidth=2.5,
            label="30-денна ковзна середня (Trend)",
            zorder=4,
        )

        # 3. Поріг спалахів (IQR Threshold)
        thresh = analysis.spikes.spike_threshold
        ax.axhline(
            y=thresh,
            color="#ef5350",
            linestyle=":",
            linewidth=1.2,
            label=f"Поріг аномалії ({thresh:.0f} переглядів)",
            zorder=3,
        )

        # 4. Підсвічування точок-спалахів
        spike_mask = work_df["views"] > thresh
        if spike_mask.any():
            spikes_dates = work_df.index[spike_mask]
            spikes_views = work_df["views"][spike_mask]
            ax.scatter(
                spikes_dates,
                spikes_views,
                color="#d32f2f",
                s=40,
                edgecolor="black",
                linewidth=0.8,
                label=f"Новинні спалахи ({analysis.spikes.spike_days_count} днів)",
                zorder=5,
            )

            # Підпис найбільшого піку
            if analysis.spikes.top_spikes:
                top_1 = analysis.spikes.top_spikes[0]
                top_dt = pd.to_datetime(top_1.date)
                ax.annotate(
                    f"{top_1.date}\n{top_1.views:,} views ({top_1.ratio_to_median}x)",
                    xy=(top_dt, top_1.views),
                    xytext=(0, 15),
                    textcoords="offset points",
                    ha="center",
                    fontsize=8,
                    fontweight="bold",
                    color="#b71c1c",
                    bbox=dict(boxstyle="round,pad=0.2", fc="#ffebee", ec="#ef9a9a", lw=0.8),
                    arrowprops=dict(arrowstyle="->", connectionstyle="arc3,rad=0", color="#b71c1c"),
                    zorder=6,
                )

        # 5. Оформлення осей
        max_y = work_df["views"].max()
        if max_y > 0:
            ax.set_ylim(0, max_y * 1.28)

        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
        fig.autofmt_xdate(rotation=30, ha="right")

        ax.set_ylabel("Кількість переглядів на день", fontsize=10, fontweight="bold", labelpad=8)
        ax.set_title(
            f"Динаміка інтересу: «{analysis.display_title}» ({analysis.project})",
            fontsize=13,
            fontweight="bold",
            pad=14,
            loc="left",
        )
        ax.grid(True, linestyle="--", alpha=0.6)
        ax.legend(loc="upper left", frameon=True, facecolor="white", edgecolor="#e0e0e0", fontsize=8.5)

        # 6. Інформаційний бейдж з ключовими метриками в правому верхньому куті
        yoy_text = f"{analysis.growth.yoy_growth_percent:+.1f}%" if analysis.growth.yoy_growth_percent is not None else "N/A"
        yoy_color = "#2e7d32" if (analysis.growth.yoy_growth_percent or 0) > 0 else "#c62828"

        kpi_lines = [
            f"Всього переглядів: {analysis.volume.total_views:,}",
            f"Середньодобові: {analysis.volume.daily_avg:.1f}",
            f"YoY зростання: {yoy_text}",
            f"Спалах-ефект: {analysis.spikes.spike_impact_percent:.1f}%",
            f"Індекс довіри: {analysis.reliability.total_score:.0f}/100 ({analysis.reliability.confidence_level})",
        ]
        kpi_box = "\n".join(kpi_lines)

        ax.text(
            0.985,
            0.96,
            kpi_box,
            transform=ax.transAxes,
            verticalalignment="top",
            horizontalalignment="right",
            fontsize=8.5,
            family="monospace",
            bbox=dict(boxstyle="round,pad=0.5", facecolor="#f8f9fa", edgecolor="#ced4da", alpha=0.95),
            zorder=6,
        )

        plt.tight_layout()
        fig.savefig(output_path, dpi=dpi)
        plt.close(fig)
        logger.info(f"Графік успішно створено: {output_path}")
        return output_path

    def generate_multi_language_chart(
        self,
        multi_res: MultiLanguageComparisonResult,
        dfs: Dict[str, pd.DataFrame],
        output_path: Optional[Path] = None,
        dpi: int = 200,
    ) -> Path:
        """
        Генерує порівняльний графік динаміки 30d Moving Average для декількох мовних розділів.
        """
        if output_path is None:
            clean_topic = multi_res.topic.replace(" ", "_").replace("/", "_")
            output_path = self.output_dir / f"multi_{clean_topic}_trend.png"

        fig, (ax_main, ax_bar) = plt.subplots(
            nrows=1,
            ncols=2,
            figsize=(11, 4.6),
            dpi=dpi,
            gridspec_kw={"width_ratios": [3.2, 1.0]},
        )

        # 1. Головний графік: 30-денні тренди для кожної мови
        color_idx = 0
        for lang, res in multi_res.results.items():
            if lang not in dfs or dfs[lang].empty:
                continue

            df = dfs[lang].copy()
            if not isinstance(df.index, pd.DatetimeIndex):
                if "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"])
                    df = df.set_index("date")
                else:
                    df.index = pd.to_datetime(df.index)

            df = df.sort_index()
            ma30 = df["views"].rolling(window=30, min_periods=7).mean()

            c = LANG_COLORS.get(lang, FALLBACK_COLORS[color_idx % len(FALLBACK_COLORS)])
            color_idx += 1

            yoy_txt = f"{res.growth.yoy_growth_percent:+.1f}%" if res.growth.yoy_growth_percent is not None else "N/A"
            lbl = f"{lang.upper()}: {res.display_title} (YoY: {yoy_txt}, Довіра: {res.reliability.total_score:.0f})"

            ax_main.plot(ma30.index, ma30, color=c, linewidth=2.2, label=lbl)

        ax_main.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        ax_main.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
        fig.autofmt_xdate(rotation=30, ha="right")

        ax_main.set_ylabel("Перегляди на день (30d MA)", fontsize=9.5, fontweight="bold")
        ax_main.set_title(
            f"Порівняльний тренд: «{multi_res.topic}»",
            fontsize=12,
            fontweight="bold",
            pad=12,
            loc="left",
        )
        ax_main.grid(True, linestyle="--", alpha=0.6)
        ax_main.legend(loc="upper left", frameon=True, facecolor="white", edgecolor="#e0e0e0", fontsize=8)

        # 2. Бокова діаграма: Порівняння часток або абсолютного обсягу ринків
        langs_list = []
        views_list = []
        colors_list = []

        # Сортуємо мови за сумарним обсягом
        sorted_res = sorted(
            multi_res.results.items(), key=lambda x: x[1].volume.total_views, reverse=True
        )
        for lang, res in sorted_res:
            langs_list.append(lang.upper())
            views_list.append(res.volume.total_views)
            colors_list.append(LANG_COLORS.get(lang, "#999999"))

        y_pos = np.arange(len(langs_list))
        bars = ax_bar.barh(y_pos, views_list, color=colors_list, alpha=0.85, height=0.55)
        ax_bar.set_yticks(y_pos)
        ax_bar.set_yticklabels(langs_list, fontweight="bold")
        ax_bar.invert_yaxis()  # Найбільший зверху
        ax_bar.set_xlabel("Сумарні перегляди", fontsize=8.5, fontweight="bold")
        ax_bar.set_title("Обсяг ринків", fontsize=10, fontweight="bold", pad=10)
        ax_bar.grid(axis="x", linestyle="--", alpha=0.5)

        for bar in bars:
            w = bar.get_width()
            ax_bar.text(
                w + (max(views_list) * 0.02 if views_list else 10),
                bar.get_y() + bar.get_height() / 2,
                f"{w:,}",
                va="center",
                fontsize=7.5,
                fontweight="bold",
            )

        # Запас для підписів барів
        if views_list:
            ax_bar.set_xlim(0, max(views_list) * 1.35)

        plt.tight_layout()
        fig.savefig(output_path, dpi=dpi)
        plt.close(fig)
        logger.info(f"Мультимовний графік успішно створено: {output_path}")
        return output_path


def main() -> None:
    """CLI для генератора графіків."""
    parser = argparse.ArgumentParser(description="Wikipedia Trend Chart Generator CLI")
    parser.add_argument("--topic", "-t", help="Тема дослідження")
    parser.add_argument("--article", "-a", help="Конкретна стаття")
    parser.add_argument("--project", "-p", default="uk.wikipedia", help="Проєкт Вікіпедії")
    parser.add_argument("--langs", "-l", help="Список мов для мультимовного графіка (наприклад 'pl,cs,uk')")
    parser.add_argument("--years", "-y", type=int, default=2, help="Кількість років (за замовчуванням 2)")
    parser.add_argument("--override", "-o", help="Ручні відповідності виду 'pl:Głodówka_lecznicza'")
    parser.add_argument("--output", help="Шлях для збереження PNG")

    args = parser.parse_args()

    client = WikimediaClient()
    resolver = TopicResolver()
    analyzer = TrendAnalyzer(wikimedia_client=client, topic_resolver=resolver)
    generator = ChartGenerator()

    # 1. Мультимовний графік
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

        dfs: Dict[str, pd.DataFrame] = {}
        for lang, res in multi_res.results.items():
            pv = client.get_article_pageviews(res.project, res.article, years=args.years)
            dfs[lang] = pv.to_dataframe()

        out_path = Path(args.output) if args.output else None
        saved = generator.generate_multi_language_chart(multi_res, dfs, output_path=out_path)
        print(f"✅ Мультимовний графік збережено: {saved}")
        return

    # 2. Одномовний графік
    art_name = args.article or args.topic
    if not art_name:
        parser.error("Необхідно вказати --article або --topic.")

    res = analyzer.analyze_article(args.project, art_name, years=args.years)
    pv = client.get_article_pageviews(args.project, art_name, years=args.years)
    df = pv.to_dataframe()

    out_path = Path(args.output) if args.output else None
    saved = generator.generate_single_article_chart(res, df, output_path=out_path)
    print(f"✅ Графік статті збережено: {saved}")


if __name__ == "__main__":
    main()
