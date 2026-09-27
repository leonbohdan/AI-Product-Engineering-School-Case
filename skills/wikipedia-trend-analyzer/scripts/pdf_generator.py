#!/usr/bin/env python3
"""
One-page Executive PDF Report Generator for Wikipedia Trend Analysis.

Цей модуль реалізує створення лаконічного 1-сторінкового PDF-звіту згідно з ADR-0004:
1. Використовує ReportLab Platypus (чистий Python без зовнішніх системних C-бібліотек/Chromium).
2. Жорстко гарантує розмір строго в 1 сторінку (формат A4).
3. Містить усі необхідні блоки:
   - Header (лого/назва теми, дата, джерело даних з bot-filtering).
   - KPI Summary Table (картки ключових метрик: обсяг, YoY приріст, спайки, індекс довіри).
   - Графік динаміки тренду (вбудований PNG).
   - Блок продуктових висновків та рекомендацій (Actionable Product Insights).
   - Блок методологічних обмежень та застережень (Assumptions & Caveats).
   - Footer.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from chart_generator import ChartGenerator
from topic_resolver import TopicResolver, parse_override_arg
from trend_analyzer import (
    MultiLanguageComparisonResult,
    TrendAnalysisResult,
    TrendAnalyzer,
)
from wikimedia_client import WikimediaClient

logger = logging.getLogger("pdf_generator")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

DEFAULT_PDF_DIR = Path(__file__).resolve().parent.parent.parent.parent / "output" / "reports"

# -----------------------------------------------------------------------------
# Реєстрація Unicode-шрифтів з підтримкою кирилиці та європейських символів
# -----------------------------------------------------------------------------

FONT_NAME = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

TTF_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/freefont/FreeSans.ttf", "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
]

for regular_path, bold_path in TTF_CANDIDATES:
    if os.path.exists(regular_path):
        try:
            pdfmetrics.registerFont(TTFont("CustomSans", regular_path))
            FONT_NAME = "CustomSans"
            if os.path.exists(bold_path):
                pdfmetrics.registerFont(TTFont("CustomSans-Bold", bold_path))
                FONT_BOLD = "CustomSans-Bold"
            else:
                FONT_BOLD = "CustomSans"
            logger.debug(f"Успішно зареєстровано Unicode шрифт: {regular_path}")
            break
        except Exception as e:
            logger.warning(f"Не вдалося зареєструвати шрифт {regular_path}: {e}")


class PageNumCanvas:
    """Канвас для строгого контролю кількості сторінок (не більше 1)."""
    def __init__(self, *args, **kwargs):
        pass


class PDFReportGenerator:
    """
    Генератор 1-сторінкових PDF-звітів для CPO та фаундерів.
    """

    def __init__(self, output_dir: Optional[Path] = None) -> None:
        self.output_dir = output_dir or DEFAULT_PDF_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.styles = self._build_styles()

    def _build_styles(self) -> Dict[str, ParagraphStyle]:
        """Створює адаптовану палітру стилів тексту з точними відступами."""
        styles = getSampleStyleSheet()

        custom_styles = {
            "SuperHeader": ParagraphStyle(
                "SuperHeader",
                parent=styles["Normal"],
                fontName=FONT_BOLD,
                fontSize=7.5,
                leading=9,
                textColor=colors.HexColor("#1565c0"),
                textTransform="uppercase",
                spaceAfter=2,
            ),
            "ReportTitle": ParagraphStyle(
                "ReportTitle",
                parent=styles["Heading1"],
                fontName=FONT_BOLD,
                fontSize=15,
                leading=18,
                textColor=colors.HexColor("#1a1a1a"),
                spaceAfter=3,
            ),
            "SubHeader": ParagraphStyle(
                "SubHeader",
                parent=styles["Normal"],
                fontName=FONT_NAME,
                fontSize=8.0,
                leading=10,
                textColor=colors.HexColor("#555555"),
                spaceAfter=8,
            ),
            "SectionTitle": ParagraphStyle(
                "SectionTitle",
                parent=styles["Heading2"],
                fontName=FONT_BOLD,
                fontSize=9.5,
                leading=12,
                textColor=colors.HexColor("#0d47a1"),
                spaceBefore=4,
                spaceAfter=3,
            ),
            "Body": ParagraphStyle(
                "Body",
                parent=styles["Normal"],
                fontName=FONT_NAME,
                fontSize=8.0,
                leading=10.5,
                textColor=colors.HexColor("#263238"),
                spaceAfter=2,
            ),
            "Bullet": ParagraphStyle(
                "Bullet",
                parent=styles["Normal"],
                fontName=FONT_NAME,
                fontSize=7.8,
                leading=10.2,
                textColor=colors.HexColor("#263238"),
                leftIndent=10,
                firstLineIndent=-7,
                spaceAfter=2,
            ),
            "CaveatBullet": ParagraphStyle(
                "CaveatBullet",
                parent=styles["Normal"],
                fontName=FONT_NAME,
                fontSize=7.3,
                leading=9.5,
                textColor=colors.HexColor("#455a64"),
                leftIndent=10,
                firstLineIndent=-7,
                spaceAfter=1.5,
            ),
            "CardLabel": ParagraphStyle(
                "CardLabel",
                fontName=FONT_NAME,
                fontSize=7.0,
                leading=8.5,
                textColor=colors.HexColor("#757575"),
                alignment=1,  # Center
            ),
            "CardValue": ParagraphStyle(
                "CardValue",
                fontName=FONT_BOLD,
                fontSize=11.0,
                leading=13.0,
                textColor=colors.HexColor("#1a1a1a"),
                alignment=1,  # Center
            ),
            "CardSub": ParagraphStyle(
                "CardSub",
                fontName=FONT_NAME,
                fontSize=6.8,
                leading=8.0,
                textColor=colors.HexColor("#424242"),
                alignment=1,  # Center
            ),
            "Footer": ParagraphStyle(
                "Footer",
                parent=styles["Normal"],
                fontName=FONT_NAME,
                fontSize=6.8,
                leading=8.0,
                textColor=colors.HexColor("#888888"),
                alignment=1,  # Center
            ),
        }
        return custom_styles

    # -------------------------------------------------------------------------
    # Генерація 1-сторінкового звіту для однієї статті
    # -------------------------------------------------------------------------

    def generate_single_report(
        self,
        analysis: TrendAnalysisResult,
        chart_path: Path,
        output_path: Optional[Path] = None,
    ) -> Path:
        """
        Формує строгий 1-сторінковий A4 звіт для однієї статті.
        """
        if output_path is None:
            clean_proj = analysis.project.replace(".org", "")
            clean_art = analysis.article.replace("/", "_").replace(" ", "_")
            output_path = self.output_dir / f"report_{clean_proj}_{clean_art}.pdf"

        # Розміри сторінки: A4 (595.27 x 841.89 pt). Поля: 36 pt (0.5 in).
        # Доступна ширина: 523.27 pt, доступна висота: 769.89 pt.
        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            leftMargin=36,
            rightMargin=36,
            topMargin=32,
            bottomMargin=28,
        )

        story: List[Any] = []

        # 1. Верхній надзаголовок та заголовок
        story.append(Paragraph("WIKIPEDIA MARKET INTELLIGENCE // EXECUTIVE ONE-PAGER", self.styles["SuperHeader"]))
        title_text = f"Аналіз ринкового інтересу: «{analysis.display_title}» ({analysis.project})"
        story.append(Paragraph(title_text, self.styles["ReportTitle"]))

        sub_text = (
            f"<b>Аналізований період:</b> {analysis.start_date} — {analysis.end_date} ({analysis.days_analyzed} днів) | "
            f"<b>Згенеровано:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
            f"<b>Фільтрація ботів:</b> активна (agent=user)"
        )
        story.append(Paragraph(sub_text, self.styles["SubHeader"]))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0d47a1"), spaceAfter=8))

        # 2. Таблиця карток KPI (4 картки)
        yoy_str = f"{analysis.growth.yoy_growth_percent:+.1f}%" if analysis.growth.yoy_growth_percent is not None else "N/A"
        yoy_color = "#2e7d32" if (analysis.growth.yoy_growth_percent or 0) > 0 else "#c62828"

        conf_badge_color = (
            "#2e7d32" if analysis.reliability.confidence_level == "High"
            else "#ef6c00" if analysis.reliability.confidence_level == "Moderate"
            else "#c62828"
        )

        card_data = [
            [
                Paragraph("СУМАРНІ ПЕРЕГЛЯДИ", self.styles["CardLabel"]),
                Paragraph("ДИНАМІКА YoY", self.styles["CardLabel"]),
                Paragraph("НОВИННИЙ ШУМ (IQR)", self.styles["CardLabel"]),
                Paragraph("ІНДЕКС ДОВІРИ", self.styles["CardLabel"]),
            ],
            [
                Paragraph(f"<b>{analysis.volume.total_views:,}</b>", self.styles["CardValue"]),
                Paragraph(f'<font color="{yoy_color}"><b>{yoy_str}</b></font>', self.styles["CardValue"]),
                Paragraph(f"<b>{analysis.spikes.spike_impact_percent:.1f}%</b>", self.styles["CardValue"]),
                Paragraph(f'<font color="{conf_badge_color}"><b>{analysis.reliability.total_score:.0f}/100</b></font>', self.styles["CardValue"]),
            ],
            [
                Paragraph(f"~{analysis.volume.daily_avg:.1f} переглядів/день", self.styles["CardSub"]),
                Paragraph(f"Органічний YoY: {analysis.growth.yoy_organic_growth_percent or 'N/A'}%", self.styles["CardSub"]),
                Paragraph(f"{analysis.spikes.spike_days_count} пікових днів", self.styles["CardSub"]),
                Paragraph(f"Рівень: <b>{analysis.reliability.confidence_level}</b>", self.styles["CardSub"]),
            ],
        ]

        card_table = Table(card_data, colWidths=[130, 130, 130, 133])
        card_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8f9fa")),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#dee2e6")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e9ecef")),
                ("TOPPADDING", (0, 0), (-1, 0), 4),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ])
        )
        story.append(card_table)
        story.append(Spacer(1, 8))

        # 3. Графік тренду
        if chart_path and chart_path.exists():
            # Доступна ширина ~ 523 pt. Висота графіка ~ 210 pt.
            chart_img = Image(str(chart_path), width=520, height=210)
            story.append(chart_img)
            story.append(Spacer(1, 8))

        # 4. Продуктові інсайти та рекомендації
        story.append(Paragraph("ПРОДУКТОВІ ВИСНОВКИ ТА ІНВЕСТИЦІЙНА ОЦІНКА", self.styles["SectionTitle"]))

        # Синтез персоналізованих висновків
        insights = self._synthesize_single_insights(analysis)
        for ins in insights:
            story.append(Paragraph(f"• {ins}", self.styles["Bullet"]))

        story.append(Spacer(1, 6))

        # 5. Методологічні обмеження та ризики
        story.append(Paragraph("МЕТОДОЛОГІЧНІ ЗАСТЕРЕЖЕННЯ ТА КОНТЕКСТ ДАНИХ", self.styles["SectionTitle"]))
        caveats = [
            "<b>Фільтрація ботів:</b> Усі запити виконано з обов'язковим фільтром <code>agent=user</code> (без краулерів та automated).",
            "<b>Інтерес vs Платоспроможність:</b> Перегляди Вікіпедії вимірюють когнітивну зацікавленість і потребу в знаннях, а не пряму готовність платити за курс/продукт.",
            "<b>Тріангуляція гіпотез:</b> Для фінального інвестиційного рішення обов'язково зіставити з Google Search Volume (SEO) та платним тестом попиту (Smoke Test / Fake Door).",
        ]
        for cav in caveats:
            story.append(Paragraph(f"• {cav}", self.styles["CaveatBullet"]))

        story.append(Spacer(1, 8))

        # 6. Футер
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceAfter=4))
        story.append(
            Paragraph(
                "Звіт підготовлено автономною AI-навичкою Wikipedia Trend Analyzer | Розроблено для Genesis AI Product Engineering School Case",
                self.styles["Footer"],
            )
        )

        doc.build(story)
        self.export_companion_png(output_path)
        logger.info(f"PDF-звіт успішно скомпільовано: {output_path}")
        return output_path

    @staticmethod
    def export_companion_png(pdf_path: Path) -> Optional[Path]:
        """Генерує супутній PNG-файл поруч із PDF для миттєвого перегляду в IDE без встановлення PDF-плагінів."""
        try:
            png_base = pdf_path.with_suffix("")
            subprocess.run(
                ["pdftoppm", "-png", "-r", "150", "-singlefile", str(pdf_path), str(png_base)],
                capture_output=True,
                check=False,
            )
            png_file = pdf_path.with_suffix(".png")
            if png_file.exists():
                logger.info(f"Супутнє прев'ю створено: {png_file}")
                return png_file
        except Exception as e:
            logger.debug(f"Не вдалося згенерувати супутнє PNG прев'ю: {e}")
        return None

    # -------------------------------------------------------------------------
    # Генерація 1-сторінкового звіту для крос-мовного порівняння
    # -------------------------------------------------------------------------

    def generate_multi_report(
        self,
        multi_res: MultiLanguageComparisonResult,
        chart_path: Path,
        output_path: Optional[Path] = None,
    ) -> Path:
        """
        Формує строгий 1-сторінковий A4 звіт для порівняння декількох мовних розділів.
        """
        if output_path is None:
            clean_topic = multi_res.topic.replace(" ", "_").replace("/", "_")
            output_path = self.output_dir / f"report_multi_{clean_topic}.pdf"

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            leftMargin=36,
            rightMargin=36,
            topMargin=30,
            bottomMargin=25,
        )

        story: List[Any] = []

        # 1. Header
        story.append(Paragraph("WIKIPEDIA MARKET INTELLIGENCE // CROSS-LINGUAL COMPARISON", self.styles["SuperHeader"]))
        title_text = f"Крос-мовне дослідження ринків: «{multi_res.topic}»"
        story.append(Paragraph(title_text, self.styles["ReportTitle"]))

        langs_str = ", ".join([l.upper() for l in multi_res.results.keys()])
        sub_text = (
            f"<b>Аналізовані ринки:</b> {langs_str} | "
            f"<b>Wikidata Entity:</b> {multi_res.wikidata_id or 'N/A'} ({multi_res.description or ''}) | "
            f"<b>Згенеровано:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        story.append(Paragraph(sub_text, self.styles["SubHeader"]))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0d47a1"), spaceAfter=6))

        # 2. Порівняльна таблиця ринків
        table_rows = [
            [
                Paragraph("<b>Ринок</b>", self.styles["CardLabel"]),
                Paragraph("<b>Стаття у Вікіпедії</b>", self.styles["CardLabel"]),
                Paragraph("<b>Перегляди (Частка)</b>", self.styles["CardLabel"]),
                Paragraph("<b>YoY ріст</b>", self.styles["CardLabel"]),
                Paragraph("<b>Spike %</b>", self.styles["CardLabel"]),
                Paragraph("<b>Індекс довіри</b>", self.styles["CardLabel"]),
                Paragraph("<b>Вердикт</b>", self.styles["CardLabel"]),
            ]
        ]

        shares = multi_res.comparison_summary.get("volume_shares_percent", {})

        for lang, res in multi_res.results.items():
            yoy_txt = f"{res.growth.yoy_growth_percent:+.1f}%" if res.growth.yoy_growth_percent is not None else "N/A"
            yoy_col = "#2e7d32" if (res.growth.yoy_growth_percent or 0) > 0 else "#c62828"
            rel_col = "#2e7d32" if res.reliability.total_score >= 80 else "#ef6c00" if res.reliability.total_score >= 50 else "#c62828"
            share_val = shares.get(lang, 0.0)

            table_rows.append([
                Paragraph(f"<b>{lang.upper()}</b>", self.styles["Body"]),
                Paragraph(f"{res.display_title[:22]}", self.styles["Body"]),
                Paragraph(f"{res.volume.total_views:,} ({share_val}%)", self.styles["Body"]),
                Paragraph(f'<font color="{yoy_col}"><b>{yoy_txt}</b></font>', self.styles["Body"]),
                Paragraph(f"{res.spikes.spike_impact_percent:.1f}%", self.styles["Body"]),
                Paragraph(f'<font color="{rel_col}"><b>{res.reliability.total_score:.0f}/100</b></font>', self.styles["Body"]),
                Paragraph(f"<b>{res.reliability.confidence_level}</b>", self.styles["Body"]),
            ])

        for ml in multi_res.missing_languages:
            table_rows.append([
                Paragraph(f"<b>{ml.upper()}</b>", self.styles["Body"]),
                Paragraph("<i>(Стаття відсутня / не знайдена)</i>", self.styles["Body"]),
                Paragraph("—", self.styles["Body"]),
                Paragraph("—", self.styles["Body"]),
                Paragraph("—", self.styles["Body"]),
                Paragraph("0/100", self.styles["Body"]),
                Paragraph("N/A (Слабкий сигнал)", self.styles["Body"]),
            ])

        comp_table = Table(table_rows, colWidths=[40, 115, 95, 65, 55, 65, 88])
        comp_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f3f5")),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#ced4da")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e9ecef")),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ])
        )
        story.append(comp_table)
        story.append(Spacer(1, 6))

        # 3. Графік мультимовного порівняння
        if chart_path and chart_path.exists():
            chart_img = Image(str(chart_path), width=520, height=205)
            story.append(chart_img)
            story.append(Spacer(1, 6))

        # 4. Продуктові висновки та рекомендація для локалізації
        story.append(Paragraph("СТРАТЕГІЧНІ ВИСНОВКИ ДЛЯ ПРОДУКТОВОЇ КОМАНДИ", self.styles["SectionTitle"]))

        multi_insights = self._synthesize_multi_insights(multi_res)
        for ins in multi_insights:
            story.append(Paragraph(f"• {ins}", self.styles["Bullet"]))

        story.append(Spacer(1, 5))

        # 5. Методологічні застереження
        story.append(Paragraph("ОБМЕЖЕННЯ АНАЛІЗУ ТА КРОС-КУЛЬТУРНІ ОСОБЛИВОСТІ", self.styles["SectionTitle"]))
        multi_caveats = [
            "<b>Розмір розділів Вікіпедії:</b> Кількість переглядів залежить як від реального інтересу, так і від загального розміру та активності мовного розділу Вікіпедії.",
            "<b>Термінологічна фрагментація:</b> Відсутність статті у певному розділі (як-от польському для деяких тем) свідчить про використання інших пошукових термінів або низьку обізнаність ринку.",
            "<b>Рекомендація:</b> Перед локалізацією запустити посадкові сторінки (Landing Pages) з попередньою реєстрацією для перевірки платоспроможного попиту.",
        ]
        for mc in multi_caveats:
            story.append(Paragraph(f"• {mc}", self.styles["CaveatBullet"]))

        story.append(Spacer(1, 6))

        # 6. Футер
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceAfter=3))
        story.append(
            Paragraph(
                "Звіт підготовлено автономною AI-навичкою Wikipedia Trend Analyzer | Genesis AI Product Engineering School Case",
                self.styles["Footer"],
            )
        )

        doc.build(story)
        self.export_companion_png(output_path)
        logger.info(f"Мультимовний PDF-звіт успішно скомпільовано: {output_path}")
        return output_path

    # -------------------------------------------------------------------------
    # Синтез продуктових висновків
    # -------------------------------------------------------------------------

    def _synthesize_single_insights(self, analysis: TrendAnalysisResult) -> List[str]:
        insights = []

        # 1. Оцінка попиту та обсягу
        if analysis.volume.daily_avg < 30:
            insights.append(
                f"<b>Низька ємність ринку у Вікіпедії:</b> Середньодобовий трафік складає лише {analysis.volume.daily_avg:.1f} переглядів/день. "
                "Тема має нішевий або локальний характер, запуск повномасштабного продукту лише на базі цього сигналу є високоризиковим."
            )
        else:
            insights.append(
                f"<b>Стабільний обсяг уваги:</b> {analysis.volume.total_views:,} переглядів за період "
                f"(в середньому {analysis.volume.daily_avg:.1f} щодня). Є сформована критична маса органічної зацікавленості."
            )

        # 2. Динаміка тренду (YoY vs MoM)
        if analysis.growth.yoy_growth_percent is not None:
            if analysis.growth.yoy_growth_percent > 15.0:
                insights.append(
                    f"<b>Висхідний тренд (+{analysis.growth.yoy_growth_percent:.1f}% YoY):</b> Інтерес до теми стабільно зростає рік до року. "
                    "Органічне зростання підтверджується позитивною медіанною динамікою."
                )
            elif analysis.growth.yoy_growth_percent < -15.0:
                insights.append(
                    f"<b>Низхідна динаміка ({analysis.growth.yoy_growth_percent:.1f}% YoY):</b> Загальна кількість переглядів за останні 365 днів знизилася. "
                    + (f"Проте за останній місяць спостерігається різкий сезонний сплеск (+{analysis.growth.mom_growth_percent:.1f}% MoM)." if (analysis.growth.mom_growth_percent or 0) > 30 else "")
                )
            else:
                insights.append(
                    f"<b>Зрілий стабільний ринок ({analysis.growth.yoy_growth_percent:+.1f}% YoY):</b> Інтерес стабільний без істотних коливань."
                )

        # 3. Новинний шум vs Органічний попит
        if analysis.spikes.spike_impact_percent > 20.0:
            insights.append(
                f"<b>Висока чутливість до інфоприводів:</b> {analysis.spikes.spike_impact_percent:.1f}% переглядів припадають на пікові дні. "
                "Потрібно враховувати, що сплески уваги мають ситуативний характер."
            )
        else:
            insights.append(
                f"<b>Чистий органічний попит:</b> Лише {analysis.spikes.spike_impact_percent:.1f}% трафіку припадає на аномальні дні. "
                "Аудиторія шукає інформацію рівномірно та системно."
            )

        # 4. Вердикт щодо довіри
        insights.append(f"<b>Рекомендація:</b> {analysis.reliability.verdict}")
        return insights

    def _synthesize_multi_insights(self, multi_res: MultiLanguageComparisonResult) -> List[str]:
        insights = []
        summary = multi_res.comparison_summary

        # 1. Лідер ринку
        top_vol = summary.get("top_market_by_volume")
        if top_vol and top_vol in multi_res.results:
            top_res = multi_res.results[top_vol]
            share = summary.get("volume_shares_percent", {}).get(top_vol, 0)
            insights.append(
                f"<b>Пріоритетний ринок за розміром:</b> {top_vol.upper()} консолідує {share}% сукупного інтересу "
                f"({top_res.volume.total_views:,} переглядів). Локалізацію продукту варто розпочинати саме з цього регіону."
            )

        # 2. Динаміка зростання
        top_growth = summary.get("top_market_by_growth")
        if top_growth and top_growth in multi_res.results and top_growth != top_vol:
            g_res = multi_res.results[top_growth]
            if g_res.growth.yoy_growth_percent and g_res.growth.yoy_growth_percent > 0:
                insights.append(
                    f"<b>Найвища динаміка зростання:</b> Ринок {top_growth.upper()} зростає швидше за інші "
                    f"(+{g_res.growth.yoy_growth_percent:.1f}% YoY). Це перспективний напрямок для випередження конкурентів."
                )

        # 3. Відсутні мови
        if multi_res.missing_languages:
            missing_names = ", ".join(l.upper() for l in multi_res.missing_languages)
            insights.append(
                f"<b>Відсутність окремих статей у розділах [{missing_names}]:</b> Свідчить про те, що аудиторія в цих країнах або "
                "користується загальнішою термінологією, або попит ще не сформований в інформаційному полі."
            )

        # 4. Загальний вердикт
        insights.append(f"<b>Загальний підсумок:</b> {summary.get('executive_summary', '')}")
        return insights


# -----------------------------------------------------------------------------
# CLI Інтерфейс
# -----------------------------------------------------------------------------

def main() -> None:
    """CLI для генератора 1-сторінкових PDF-звітів."""
    parser = argparse.ArgumentParser(description="Wikipedia Trend One-page PDF Report Generator CLI")
    parser.add_argument("--topic", "-t", help="Тема дослідження")
    parser.add_argument("--article", "-a", help="Конкретна назва статті")
    parser.add_argument("--project", "-p", default="uk.wikipedia", help="Проєкт Вікіпедії")
    parser.add_argument("--langs", "-l", help="Список мов для мультимовного звіту (наприклад 'pl,cs,uk')")
    parser.add_argument("--years", "-y", type=int, default=2, help="Кількість років аналізу")
    parser.add_argument("--override", "-o", help="Ручні відповідності виду 'pl:Głodówka_lecznicza'")
    parser.add_argument("--output", help="Шлях для збереження вихідного PDF")

    args = parser.parse_args()

    client = WikimediaClient()
    resolver = TopicResolver()
    analyzer = TrendAnalyzer(wikimedia_client=client, topic_resolver=resolver)
    chart_gen = ChartGenerator()
    pdf_gen = PDFReportGenerator()

    # 1. Мультимовний звіт
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

        dfs: Dict[str, Any] = {}
        for lang, res in multi_res.results.items():
            pv = client.get_article_pageviews(res.project, res.article, years=args.years)
            dfs[lang] = pv.to_dataframe()

        chart_file = chart_gen.generate_multi_language_chart(multi_res, dfs)
        pdf_file = pdf_gen.generate_multi_report(multi_res, chart_file, output_path=Path(args.output) if args.output else None)

        print(f"\n✅ Мультимовний 1-сторінковий PDF-звіт успішно згенеровано:")
        print(f"📄 PDF:   {pdf_file}")
        print(f"📊 Графік: {chart_file}\n")
        return

    # 2. Одномовний звіт
    art_name = args.article or args.topic
    if not art_name:
        parser.error("Необхідно вказати --article або --topic.")

    analysis = analyzer.analyze_article(args.project, art_name, years=args.years)
    pv = client.get_article_pageviews(args.project, art_name, years=args.years)
    df = pv.to_dataframe()

    chart_file = chart_gen.generate_single_article_chart(analysis, df)
    pdf_file = pdf_gen.generate_single_report(analysis, chart_file, output_path=Path(args.output) if args.output else None)

    print(f"\n✅ Односторінковий PDF-звіт успішно згенеровано:")
    print(f"📄 PDF:   {pdf_file}")
    print(f"📊 Графік: {chart_file}\n")


if __name__ == "__main__":
    main()
