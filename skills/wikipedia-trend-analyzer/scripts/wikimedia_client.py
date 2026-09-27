#!/usr/bin/env python3
"""
Wikimedia Pageviews REST API Client.

Цей модуль забезпечує роботу з Wikimedia Analytics API (Pageviews per article)
відповідно до вимог ADR-0001, ADR-0003 та політики Wikimedia API.

Особливості:
- Обов'язковий User-Agent за стандартами Wikimedia Foundation.
- Фільтрація ботів за замовчуванням (agent='user').
- Нормалізація проектів (uk -> uk.wikipedia) та кодування назв статей.
- Інтеграція з pandas DataFrame для подальшого аналізу.
- Вбудоване кешування запитів для прискорення та уникнення лімітів API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Налаштування логування
logger = logging.getLogger("wikimedia_client")
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Константи за замовчуванням
DEFAULT_BASE_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
DEFAULT_USER_AGENT = (
    "WikipediaTrendAnalyzer/1.0 "
    "(https://github.com/leonbohdan/AI-Product-Engineering-School-Case; "
    "contact: product-engineering-school@example.com)"
)
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "wikimedia"


class WikimediaAPIError(Exception):
    """Базовий клас для помилок Wikimedia API."""
    pass


class ArticleNotFoundError(WikimediaAPIError):
    """Помилка 404: статтю не знайдено або відсутні перегляди за вказаний період."""
    pass


class RateLimitError(WikimediaAPIError):
    """Помилка 429: перевищено ліміт запитів до Wikimedia API."""
    pass


@dataclass(frozen=True)
class DailyPageView:
    """Модель одного дня переглядів статті."""
    date: str  # YYYY-MM-DD
    timestamp: str  # YYYYMMDD00
    views: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PageViewsResult:
    """Результат запиту переглядів статті."""
    project: str
    article: str
    start: str
    end: str
    granularity: str
    access: str
    agent: str
    items: List[DailyPageView]
    total_views: int

    def to_dataframe(self) -> pd.DataFrame:
        """Перетворює щоденні перегляди в pandas.DataFrame з datetime-індексом."""
        if not self.items:
            return pd.DataFrame(columns=["date", "views"]).set_index("date")
        
        df = pd.DataFrame([{"date": item.date, "views": item.views} for item in self.items])
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        return df

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project": self.project,
            "article": self.article,
            "start": self.start,
            "end": self.end,
            "granularity": self.granularity,
            "access": self.access,
            "agent": self.agent,
            "total_views": self.total_views,
            "items_count": len(self.items),
            "items": [item.to_dict() for item in self.items],
        }


class WikimediaClient:
    """
    Клієнт для взаємодії з Wikimedia Pageviews API.
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        base_url: str = DEFAULT_BASE_URL,
        cache_dir: Optional[Path] = DEFAULT_CACHE_DIR,
        enable_cache: bool = True,
        timeout: int = 15,
        max_retries: int = 3,
    ) -> None:
        self.user_agent = user_agent
        self.base_url = base_url.rstrip("/")
        self.cache_dir = cache_dir
        self.enable_cache = enable_cache
        self.timeout = timeout

        if self.enable_cache and self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Налаштування HTTP-сесії з автоматичними повторами
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        })

        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    @staticmethod
    def normalize_project(project: str) -> str:
        """
        Нормалізує ідентифікатор мови/проекту до формату Wikimedia.
        Наприклад:
        - 'uk' -> 'uk.wikipedia'
        - 'uk.wikipedia.org' -> 'uk.wikipedia'
        - 'pl.wikipedia' -> 'pl.wikipedia'
        """
        project = project.strip().lower()
        if project.endswith(".org"):
            project = project[:-4]
        if "." not in project:
            project = f"{project}.wikipedia"
        return project

    @staticmethod
    def normalize_article_title(article: str) -> str:
        """
        Нормалізує назву статті:
        - Замінює пробіли на підкреслення.
        - Робить велику першу літеру (традиція MediaWiki).
        """
        article = article.strip()
        if not article:
            raise ValueError("Назва статті не може бути порожньою.")
        article = article.replace(" ", "_")
        # Велика перша літера, якщо це звичайний символ
        if article and not article.startswith("%"):
            article = article[0].upper() + article[1:]
        return article

    @staticmethod
    def format_timestamp(date_input: Any) -> str:
        """
        Приводить рядок дати або datetime до формату YYYYMMDD00.
        Приймає:
        - 'YYYY-MM-DD'
        - 'YYYYMMDD'
        - 'YYYYMMDD00'
        - datetime / date
        """
        if isinstance(date_input, (datetime, pd.Timestamp)):
            return date_input.strftime("%Y%m%d00")
        
        s = str(date_input).strip()
        # Якщо передано з дефісами (2024-05-15)
        s_clean = s.replace("-", "").replace("/", "")
        if len(s_clean) == 8:
            return f"{s_clean}00"
        elif len(s_clean) == 10:
            return s_clean
        else:
            raise ValueError(f"Невірний формат дати: {date_input}. Очікується YYYY-MM-DD або YYYYMMDD.")

    @staticmethod
    def timestamp_to_date_str(timestamp: str) -> str:
        """Конвертує YYYYMMDD00 у читабельний рядок YYYY-MM-DD."""
        clean = timestamp[:8]
        return f"{clean[:4]}-{clean[4:6]}-{clean[6:8]}"

    @classmethod
    def calculate_date_range(cls, years: int = 2) -> Tuple[str, str]:
        """
        Розраховує діапазон дат за останні N років від учорашнього дня
        (оскільки статистика за сьогодні ще не закрита).
        Повертає кортеж (start_timestamp, end_timestamp).
        """
        today = datetime.now(timezone.utc)
        yesterday = today - timedelta(days=1)
        # N років назад
        try:
            start_date = yesterday.replace(year=yesterday.year - years)
        except ValueError:
            # Обробка високосного 29 лютого
            start_date = yesterday.replace(year=yesterday.year - years, day=28)
            
        return cls.format_timestamp(start_date), cls.format_timestamp(yesterday)

    def _get_cache_key(self, project: str, article: str, access: str, agent: str,
                        granularity: str, start: str, end: str) -> str:
        """Генерує унікальний ключ для кешування."""
        raw = f"{project}:{article}:{access}:{agent}:{granularity}:{start}:{end}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _read_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Читає відповідь з локального дискового кешу."""
        if not self.enable_cache or not self.cache_dir:
            return None
        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Не вдалося прочитати кеш {cache_file}: {e}")
        return None

    def _write_cache(self, cache_key: str, data: Dict[str, Any]) -> None:
        """Зберігає відповідь у локальний кеш."""
        if not self.enable_cache or not self.cache_dir:
            return
        cache_file = self.cache_dir / f"{cache_key}.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Не вдалося записати кеш {cache_file}: {e}")

    def get_article_pageviews(
        self,
        project: str,
        article: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        years: int = 2,
        access: str = "all-access",
        agent: str = "user",  # ADR-0003: за замовчуванням тільки реальні користувачі
        granularity: str = "daily",
        use_cache: bool = True,
    ) -> PageViewsResult:
        """
        Отримує статистику переглядів конкретної статті за період.

        :param project: Мовний розділ (наприклад 'uk', 'pl', 'en' або 'uk.wikipedia')
        :param article: Назва статті (наприклад 'Астрономія' або 'Post_przerywany')
        :param start: Початкова дата (YYYY-MM-DD або YYYYMMDD00). Якщо None, обчислюється через years.
        :param end: Кінцева дата (YYYY-MM-DD або YYYYMMDD00). Якщо None, береться вчора.
        :param years: Кількість років для аналізу, якщо start не вказано.
        :param access: Тип доступу ('all-access', 'desktop', 'mobile-web', 'mobile-app').
        :param agent: Тип агента ('user', 'spider', 'automated', 'all-agents').
        :param granularity: Гранулярність ('daily', 'monthly').
        :param use_cache: Чи використовувати локальний кеш.
        :return: PageViewsResult об'єкт.
        """
        norm_project = self.normalize_project(project)
        norm_article = self.normalize_article_title(article)

        if not start or not end:
            def_start, def_end = self.calculate_date_range(years=years)
            start = self.format_timestamp(start) if start else def_start
            end = self.format_timestamp(end) if end else def_end
        else:
            start = self.format_timestamp(start)
            end = self.format_timestamp(end)

        # Кодування статті для URL: слеші та спецсимволи мають бути закодовані
        encoded_article = urllib.parse.quote(norm_article, safe="")

        cache_key = self._get_cache_key(
            norm_project, norm_article, access, agent, granularity, start, end
        )

        if use_cache:
            cached_data = self._read_cache(cache_key)
            if cached_data is not None:
                logger.debug(f"Кеш попадання для {norm_project}/{norm_article}")
                return self._parse_api_response(
                    cached_data, norm_project, norm_article, start, end, granularity, access, agent
                )

        url = f"{self.base_url}/{norm_project}/{access}/{agent}/{encoded_article}/{granularity}/{start}/{end}"
        logger.info(f"Запит до Wikimedia API: {url}")

        try:
            resp = self.session.get(url, timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            raise WikimediaAPIError(f"Мережева помилка при зверненні до Wikimedia API: {e}") from e

        if resp.status_code == 404:
            raise ArticleNotFoundError(
                f"Статтю '{norm_article}' не знайдено в проекті '{norm_project}' або перегляди відсутні за період {start}-{end}."
            )
        elif resp.status_code == 429:
            raise RateLimitError("Перевищено ліміт запитів до Wikimedia API (HTTP 429).")
        elif resp.status_code != 200:
            raise WikimediaAPIError(
                f"Помилка Wikimedia API (HTTP {resp.status_code}): {resp.text}"
            )

        try:
            data = resp.json()
        except ValueError as e:
            raise WikimediaAPIError(f"Некоректна JSON-відповідь від API: {e}") from e

        if use_cache:
            self._write_cache(cache_key, data)

        return self._parse_api_response(
            data, norm_project, norm_article, start, end, granularity, access, agent
        )

    def _parse_api_response(
        self,
        data: Dict[str, Any],
        project: str,
        article: str,
        start: str,
        end: str,
        granularity: str,
        access: str,
        agent: str,
    ) -> PageViewsResult:
        """Парсить JSON-відповідь у типізований PageViewsResult."""
        raw_items = data.get("items", [])
        items: List[DailyPageView] = []
        total_views = 0

        for item in raw_items:
            ts = str(item.get("timestamp", ""))
            views = int(item.get("views", 0))
            date_str = self.timestamp_to_date_str(ts)
            items.append(DailyPageView(date=date_str, timestamp=ts, views=views))
            total_views += views

        return PageViewsResult(
            project=project,
            article=article,
            start=start,
            end=end,
            granularity=granularity,
            access=access,
            agent=agent,
            items=items,
            total_views=total_views,
        )


def plot_pageviews(
    result: PageViewsResult,
    output_path: Optional[Path] = None,
) -> Path:
    """
    Будує та зберігає графік переглядів статті у форматі PNG (300 DPI).
    Відображає щоденні перегляди, 30-денне згладжування та аномальні спалахи.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    df = result.to_dataframe()
    if df.empty:
        raise ValueError("Неможливо побудувати графік: набір даних порожній.")

    # Розрахунок 30-денної ковзної середньої
    df["ma30"] = df["views"].rolling(window=30, min_periods=7).mean()

    # Детекція спалахів (IQR метод)
    q25 = df["views"].quantile(0.25)
    q75 = df["views"].quantile(0.75)
    iqr = q75 - q25
    spike_threshold = q75 + 1.5 * iqr
    outliers = df[df["views"] > spike_threshold]

    # Визначення шляху збереження
    if output_path is None:
        charts_dir = Path(__file__).resolve().parent.parent.parent.parent / "output" / "charts"
        charts_dir.mkdir(parents=True, exist_ok=True)
        safe_title = re.sub(r'[^\w\-_.]', '_', result.article)
        output_path = charts_dir / f"{result.project}_{safe_title}.png"
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(12, 6), dpi=300)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#f9fafb")

    # 1. Сирі щоденні перегляди (напівпрозорий фон)
    ax.plot(
        df.index,
        df["views"],
        color="#94a3b8",
        alpha=0.45,
        linewidth=1.0,
        label="Щоденні перегляди (Raw daily views)",
    )
    ax.fill_between(df.index, df["views"], color="#cbd5e1", alpha=0.15)

    # 2. 30-денний згладжений тренд
    ax.plot(
        df.index,
        df["ma30"],
        color="#2563eb",
        linewidth=2.5,
        label="30-денний тренд (30d Moving Average)",
    )

    # 3. Маркери спалахів (outliers)
    if not outliers.empty:
        ax.scatter(
            outliers.index,
            outliers["views"],
            color="#ef4444",
            s=40,
            zorder=5,
            label=f"Аномальні спалахи (Outliers, n={len(outliers)})",
        )

    # Форматування заголовка та осей
    date_start_str = df.index.min().strftime("%d.%m.%Y")
    date_end_str = df.index.max().strftime("%d.%m.%Y")
    ax.set_title(
        f"Динаміка переглядів статті: «{result.article}» ({result.project})\n"
        f"Період: {date_start_str} — {date_end_str} | Всього: {result.total_views:,} переглядів (агенти: {result.agent})",
        fontsize=13,
        fontweight="bold",
        pad=15,
        color="#0f172a",
    )
    ax.set_xlabel("Дата", fontsize=11, labelpad=10, color="#334155")
    ax.set_ylabel("Кількість переглядів на день", fontsize=11, labelpad=10, color="#334155")

    # Форматування осі X (дати)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    plt.xticks(rotation=0, ha="center")

    # Форматування осі Y (кома як розділювач тисяч)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"{int(x):,}"))

    # Сітка та рамка
    ax.grid(True, linestyle="--", alpha=0.5, color="#cbd5e1")
    for spine in ax.spines.values():
        spine.set_color("#cbd5e1")

    # Легенда
    ax.legend(loc="upper left", frameon=True, facecolor="#ffffff", edgecolor="#cbd5e1", fontsize=10)

    # Додаткова інфо-панель (KPI)
    avg_views = df["views"].mean()
    median_views = df["views"].median()
    max_views = df["views"].max()
    max_date = df["views"].idxmax().strftime("%d.%m.%Y")
    info_text = (
        f"Середньодобово: {avg_views:.1f}\n"
        f"Медіана: {median_views:.1f}\n"
        f"Максимум: {max_views:,} ({max_date})"
    )
    ax.text(
        0.98,
        0.95,
        info_text,
        transform=ax.transAxes,
        fontsize=9,
        verticalalignment="top",
        horizontalalignment="right",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="#ffffff", edgecolor="#cbd5e1", alpha=0.9),
    )

    plt.tight_layout()
    plt.savefig(output_path, dpi=300)
    plt.close(fig)

    logger.info(f"Графік збережено: {output_path}")
    return output_path


def main() -> None:
    """CLI інтерфейс для швидкого тестування клієнта."""
    parser = argparse.ArgumentParser(description="Отримання переглядів статті з Wikimedia Pageviews API.")
    parser.add_argument("--project", "-p", required=True, help="Мовний проект (наприклад 'uk', 'pl', 'cs', 'en')")
    parser.add_argument("--article", "-a", required=True, help="Назва статті (наприклад 'Астрономія')")
    parser.add_argument("--years", "-y", type=int, default=2, help="Кількість років аналізу (за замовчуванням 2)")
    parser.add_argument("--start", help="Початкова дата YYYY-MM-DD")
    parser.add_argument("--end", help="Кінцева дата YYYY-MM-DD")
    parser.add_argument("--agent", default="user", choices=["user", "all-agents", "spider", "automated"],
                        help="Фільтр агентів (за замовчуванням 'user')")
    parser.add_argument("--json", action="store_true", help="Вивести повний JSON замість короткого зведення")
    parser.add_argument("--plot", action="store_true", help="Побудувати та зберегти графік переглядів у PNG")
    parser.add_argument("--output-chart", help="Шлях для збереження графіка (PNG)")

    args = parser.parse_args()

    client = WikimediaClient()
    try:
        res = client.get_article_pageviews(
            project=args.project,
            article=args.article,
            start=args.start,
            end=args.end,
            years=args.years,
            agent=args.agent,
        )
    except ArticleNotFoundError as e:
        print(f"Помилка: {e}", file=sys.stderr)
        sys.exit(1)
    except WikimediaAPIError as e:
        print(f"Помилка API: {e}", file=sys.stderr)
        sys.exit(2)

    chart_file = None
    if args.plot or args.output_chart:
        target_path = Path(args.output_chart) if args.output_chart else None
        try:
            chart_file = plot_pageviews(res, target_path)
        except Exception as e:
            logger.error(f"Помилка побудови графіка: {e}")

    if args.json:
        data = res.to_dict()
        if chart_file:
            data["chart_path"] = str(chart_file)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        df = res.to_dataframe()
        print(f"=== Результати для статті '{res.article}' ({res.project}) ===")
        print(f"Період: {res.items[0].date if res.items else 'N/A'} — {res.items[-1].date if res.items else 'N/A'}")
        print(f"Фільтр агентів: {res.agent} (виключено ботів)")
        print(f"Всього днів: {len(res.items)}")
        print(f"Сумарно переглядів: {res.total_views:,}")
        if not df.empty:
            print(f"Середньодобово: {df['views'].mean():.1f}")
            print(f"Медіана на день: {df['views'].median():.1f}")
            print(f"Мінімум: {df['views'].min()} | Максимум: {df['views'].max()}")
        if chart_file:
            print(f"\n📊 Графік успішно збережено: {chart_file}")


if __name__ == "__main__":
    main()

