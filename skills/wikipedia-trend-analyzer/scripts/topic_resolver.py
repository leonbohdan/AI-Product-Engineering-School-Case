#!/usr/bin/env python3
"""
Cross-lingual Topic & Entity Resolver for Wikipedia.

Цей модуль реалізує стратегію крос-мовного зіставлення сутностей згідно з ADR-0002:
1. Пошук базової статті у Вікіпедії мовою запиту (або англійській).
2. Отримання унікального ідентифікатора сутності Wikidata (Q-number, наприклад Q1666254).
3. Витягнення точних офіційних назв статей для всіх цільових мов через Wikidata sitelinks.
4. Пошук альтернативних кандидатів (fallback), якщо у цільовому мовному розділі окремої статті немає.
5. Підтримка явних перевизначень (overrides) користувачем або агентом.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Налаштування логування
logger = logging.getLogger("topic_resolver")
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

DEFAULT_USER_AGENT = (
    "WikipediaTrendAnalyzer/1.0 "
    "(https://github.com/leonbohdan/AI-Product-Engineering-School-Case; "
    "contact: product-engineering-school@example.com)"
)
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "wikidata"


@dataclass
class ResolvedArticle:
    """Результат резолву назви статті для конкретної мови."""
    lang: str                     # Код мови (uk, pl, cs, en тощо)
    project: str                  # uk.wikipedia, pl.wikipedia тощо
    title: Optional[str]          # Точна назва статті (з підкресленнями)
    display_title: Optional[str]  # Назва для відображення (з пробілами)
    url: Optional[str]            # Повний URL статті у Вікіпедії
    found: bool                   # Чи знайдено статтю
    source: str                   # 'wikidata_sitelink', 'manual_override', 'search_fallback', 'not_found'
    candidates: List[str]         # Споріднені теми, якщо прямої статті немає
    notes: Optional[str] = None   # Коментар (наприклад, чому стаття відсутня)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TopicResolutionResult:
    """Підсумковий результат крос-мовного резолву сутності."""
    query: str
    base_lang: str
    base_title: str
    wikidata_id: Optional[str]
    description: Optional[str]
    articles: Dict[str, ResolvedArticle]

    def get_article_title(self, lang: str) -> Optional[str]:
        """Повертає точну назву статті для вказаної мови або None."""
        art = self.articles.get(lang.lower())
        return art.title if art and art.found else None

    def get_found_languages(self) -> List[str]:
        """Список мов, для яких стаття реально існує."""
        return [lang for lang, art in self.articles.items() if art.found]

    def get_missing_languages(self) -> List[str]:
        """Список мов, для яких окремої статті у Вікіпедії не існує."""
        return [lang for lang, art in self.articles.items() if not art.found]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "base_lang": self.base_lang,
            "base_title": self.base_title,
            "wikidata_id": self.wikidata_id,
            "description": self.description,
            "found_languages": self.get_found_languages(),
            "missing_languages": self.get_missing_languages(),
            "articles": {k: v.to_dict() for k, v in self.articles.items()},
        }


class TopicResolver:
    """
    Резолвер сутностей та крос-мовних назв статей Вікіпедії через Wikidata API.
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        cache_dir: Optional[Path] = DEFAULT_CACHE_DIR,
        enable_cache: bool = True,
        timeout: int = 10,
    ) -> None:
        self.user_agent = user_agent
        self.cache_dir = cache_dir
        self.enable_cache = enable_cache
        self.timeout = timeout

        if self.enable_cache and self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        })

        retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)

    @staticmethod
    def detect_base_lang(text: str) -> str:
        """
        Визначає базову мову за символами тексту:
        - Якщо є кирилиця (і/ї/є/ґ тощо) -> 'uk'
        - Інакше за замовчуванням 'en'
        """
        has_cyrillic = bool(re.search(r'[\u0400-\u04FF]', text))
        return "uk" if has_cyrillic else "en"

    def _get_cache(self, key: str) -> Optional[Dict[str, Any]]:
        if not self.enable_cache or not self.cache_dir:
            return None
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return None

    def _set_cache(self, key: str, data: Dict[str, Any]) -> None:
        if not self.enable_cache or not self.cache_dir:
            return
        cache_file = self.cache_dir / f"{key}.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    def search_wikipedia_article(self, query: str, lang: str = "uk") -> Optional[str]:
        """
        Шукає найбільш релевантну статтю у Вікіпедії вказаною мовою (OpenSearch).
        Повертає назву статті (з пробілами) або None.
        """
        clean_query = query.strip()
        url = f"https://{lang}.wikipedia.org/w/api.php"
        params = {
            "action": "opensearch",
            "search": clean_query,
            "limit": 1,
            "namespace": 0,
            "format": "json",
        }

        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                # data format: [query, [titles], [descriptions], [urls]]
                if len(data) > 1 and data[1]:
                    return data[1][0]
        except Exception as e:
            logger.warning(f"Помилка OpenSearch у {lang}.wikipedia для '{clean_query}': {e}")

        # Fallback: спроба звичайного повнотекстового пошуку API
        try:
            search_params = {
                "action": "query",
                "list": "search",
                "srsearch": clean_query,
                "srlimit": 1,
                "format": "json",
            }
            resp = self.session.get(url, params=search_params, timeout=self.timeout)
            if resp.status_code == 200:
                s_data = resp.json()
                results = s_data.get("query", {}).get("search", [])
                if results:
                    return results[0]["title"]
        except Exception as e:
            logger.warning(f"Помилка fallback пошуку у {lang}.wikipedia для '{clean_query}': {e}")

        return None

    def get_wikidata_item_id(self, title: str, lang: str = "uk") -> Optional[str]:
        """
        Отримує Wikidata Item ID (Q-номер) для відомої назви статті у Вікіпедії.
        """
        url = f"https://{lang}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "prop": "pageprops",
            "titles": title,
            "redirects": 1,
            "format": "json",
        }

        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                pages = data.get("query", {}).get("pages", {})
                for page_id, page_data in pages.items():
                    if page_id != "-1":
                        props = page_data.get("pageprops", {})
                        return props.get("wikibase_item")
        except Exception as e:
            logger.warning(f"Не вдалося отримати Wikidata ID для '{title}' ({lang}): {e}")

        return None

    def get_wikidata_sitelinks_and_desc(
        self, wikidata_id: str
    ) -> Tuple[Dict[str, str], Optional[str]]:
        """
        Отримує всі прив'язані мовні розділи Вікіпедії (sitelinks) та опис для сутності.
        Повертає словник {lang_code: title} та опис (наприклад, uk/en).
        """
        url = "https://www.wikidata.org/w/api.php"
        params = {
            "action": "wbgetentities",
            "ids": wikidata_id,
            "props": "sitelinks|descriptions",
            "languages": "uk|en",
            "format": "json",
        }

        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                entity = data.get("entities", {}).get(wikidata_id, {})
                sitelinks_raw = entity.get("sitelinks", {})

                # Мапінг: "ukwiki" -> "uk", "plwiki" -> "pl"
                sitelinks: Dict[str, str] = {}
                for site, item in sitelinks_raw.items():
                    if site.endswith("wiki") and not site.startswith(("commons", "species", "sources")):
                        lang_code = site[:-4]
                        sitelinks[lang_code] = item.get("title", "")

                descriptions = entity.get("descriptions", {})
                desc = None
                if "uk" in descriptions:
                    desc = descriptions["uk"].get("value")
                elif "en" in descriptions:
                    desc = descriptions["en"].get("value")

                return sitelinks, desc
        except Exception as e:
            logger.warning(f"Помилка отримання sitelinks для Wikidata ID {wikidata_id}: {e}")

        return {}, None

    def search_candidates_in_lang(self, query: str, target_lang: str, limit: int = 3) -> List[str]:
        """
        Шукає схожі статті у цільовому мовному розділі, якщо прямий лінк відсутній.
        """
        url = f"https://{target_lang}.wikipedia.org/w/api.php"
        params = {
            "action": "opensearch",
            "search": query,
            "limit": limit,
            "format": "json",
        }
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                if len(data) > 1 and data[1]:
                    return data[1]
        except Exception:
            pass
        return []

    def resolve(
        self,
        topic: str,
        target_langs: List[str],
        base_lang: Optional[str] = None,
        overrides: Optional[Dict[str, str]] = None,
    ) -> TopicResolutionResult:
        """
        Повний цикл крос-мовного резолву теми для списку мов.

        :param topic: Назва теми (наприклад 'інтервальне голодування', 'астрономія', 'English learning')
        :param target_langs: Список цільових мов (наприклад ['pl', 'cs', 'uk', 'en'])
        :param base_lang: Базова мова для первинного пошуку (якщо None, автовизначення)
        :param overrides: Словник явних відповідностей {'pl': 'Głodówka_lecznicza'}
        :return: TopicResolutionResult
        """
        topic = topic.strip()
        overrides = {k.lower(): v.strip() for k, v in (overrides or {}).items()}
        target_langs = [l.strip().lower() for l in target_langs if l.strip()]

        if not base_lang:
            base_lang = self.detect_base_lang(topic)

        # Перевірка кешу
        cache_key = hashlib.sha256(
            f"{topic}:{','.join(sorted(target_langs))}:{base_lang}:{json.dumps(overrides)}".encode("utf-8")
        ).hexdigest()
        cached = self._get_cache(cache_key)
        if cached:
            articles_map: Dict[str, ResolvedArticle] = {}
            for k, v in cached.get("articles", {}).items():
                articles_map[k] = ResolvedArticle(**v)
            return TopicResolutionResult(
                query=cached["query"],
                base_lang=cached["base_lang"],
                base_title=cached["base_title"],
                wikidata_id=cached["wikidata_id"],
                description=cached["description"],
                articles=articles_map,
            )

        logger.info(f"Початок резолву теми '{topic}' для мов {target_langs} (базова мова: {base_lang})")

        # 1. Знайти назву статті базовою мовою
        base_title = self.search_wikipedia_article(topic, lang=base_lang)
        if not base_title and base_lang != "en":
            # Спроба пошуку в англійській Вікіпедії, якщо в базовій не знайдено
            logger.info(f"Тему не знайдено в {base_lang}.wikipedia, спроба пошуку в en.wikipedia...")
            base_title = self.search_wikipedia_article(topic, lang="en")
            if base_title:
                base_lang = "en"

        if not base_title:
            base_title = topic  # Fallback

        # 2. Отримати Wikidata Item ID
        wikidata_id = self.get_wikidata_item_id(base_title, lang=base_lang)

        # 3. Отримати sitelinks з Wikidata
        sitelinks: Dict[str, str] = {}
        description: Optional[str] = None
        if wikidata_id:
            sitelinks, description = self.get_wikidata_sitelinks_and_desc(wikidata_id)
            logger.info(f"Знайдено Wikidata сутність {wikidata_id} з {len(sitelinks)} мовними розділами")

        # 4. Сформувати резолв для кожної цільової мови
        articles: Dict[str, ResolvedArticle] = {}

        for lang in target_langs:
            project = f"{lang}.wikipedia"

            # А) Перевірка ручного перевизначення
            if lang in overrides and overrides[lang]:
                override_title = overrides[lang].replace(" ", "_")
                articles[lang] = ResolvedArticle(
                    lang=lang,
                    project=project,
                    title=override_title,
                    display_title=override_title.replace("_", " "),
                    url=f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(override_title)}",
                    found=True,
                    source="manual_override",
                    candidates=[],
                    notes="Задано вручну через параметри override.",
                )
                continue

            # Б) Пошук точної статті у sitelinks Wikidata
            if lang in sitelinks and sitelinks[lang]:
                found_title = sitelinks[lang].replace(" ", "_")
                articles[lang] = ResolvedArticle(
                    lang=lang,
                    project=project,
                    title=found_title,
                    display_title=found_title.replace("_", " "),
                    url=f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(found_title)}",
                    found=True,
                    source="wikidata_sitelink",
                    candidates=[],
                    notes=f"Офіційна стаття зв'язана у Wikidata ({wikidata_id}).",
                )
                continue

            # В) Якщо базовою мовою стаття співпадає з цільовою
            if lang == base_lang and base_title:
                norm_title = base_title.replace(" ", "_")
                articles[lang] = ResolvedArticle(
                    lang=lang,
                    project=project,
                    title=norm_title,
                    display_title=norm_title.replace("_", " "),
                    url=f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(norm_title)}",
                    found=True,
                    source="base_language_match",
                    candidates=[],
                    notes="Стаття знайдена в базовому розділі запиту.",
                )
                continue

            # Г) Fallback: стаття відсутня у Wikidata для цієї мови
            logger.warning(f"У розділі {project} немає прив'язаної статті для теми '{topic}'. Шукаємо схожі теми...")
            candidates = self.search_candidates_in_lang(topic, lang, limit=3)
            notes_text = (
                f"У мовному розділі {project} відсутня окрема стаття за сутністю {wikidata_id or 'N/A'}. "
                "Це свідчить про низьку представленість або відсутність сформованої термінології у Вікіпедії."
            )
            if candidates:
                notes_text += f" Споріднені теми в розділі: {', '.join(candidates)}."

            articles[lang] = ResolvedArticle(
                lang=lang,
                project=project,
                title=None,
                display_title=None,
                url=None,
                found=False,
                source="not_found",
                candidates=candidates,
                notes=notes_text,
            )

        result = TopicResolutionResult(
            query=topic,
            base_lang=base_lang,
            base_title=base_title,
            wikidata_id=wikidata_id,
            description=description,
            articles=articles,
        )

        self._set_cache(cache_key, result.to_dict())
        return result


def parse_override_arg(override_str: Optional[str]) -> Dict[str, str]:
    """
    Парсить рядок аргументу виду 'pl:Głodówka_lecznicza,cs:Půst' у словник.
    """
    overrides: Dict[str, str] = {}
    if not override_str:
        return overrides
    parts = override_str.split(",")
    for p in parts:
        if ":" in p:
            k, v = p.split(":", 1)
            overrides[k.strip().lower()] = v.strip()
    return overrides


def main() -> None:
    """CLI інтерфейс для резолвера тем."""
    parser = argparse.ArgumentParser(description="Крос-мовний резолвер статей Вікіпедії через Wikidata API.")
    parser.add_argument("--topic", "-t", required=True, help="Тема для дослідження (наприклад 'інтервальне голодування', 'астрономія')")
    parser.add_argument("--langs", "-l", required=True, help="Список цільових мов через кому (наприклад 'pl,cs,uk,en')")
    parser.add_argument("--base-lang", "-b", help="Базова мова запиту (за замовчуванням автовизначення)")
    parser.add_argument("--override", "-o", help="Ручні відповідності виду 'pl:Głodówka_lecznicza,cs:Půst'")
    parser.add_argument("--json", action="store_true", help="Вивести повний JSON замість таблиці")

    args = parser.parse_args()

    target_langs = [l.strip() for l in args.langs.split(",") if l.strip()]
    overrides = parse_override_arg(args.override)

    resolver = TopicResolver()
    res = resolver.resolve(
        topic=args.topic,
        target_langs=target_langs,
        base_lang=args.base_lang,
        overrides=overrides,
    )

    if args.json:
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"\n🔍 Результати крос-мовного резолву для теми: «{res.query}»")
        print(f"Базова стаття: {res.base_title} ({res.base_lang})")
        print(f"Wikidata Item ID: {res.wikidata_id or 'Не знайдено'}")
        if res.description:
            print(f"Опис сутності: {res.description}")
        print("\n" + "=" * 90)
        print(f"{'Мова':<6} | {'Статус':<10} | {'Точна назва статті':<30} | {'Джерело / Коментар'}")
        print("-" * 90)

        for lang, art in res.articles.items():
            status = "✅ Знайдено" if art.found else "❌ Відсутня"
            title = art.title or "—"
            comment = art.source
            if not art.found and art.candidates:
                comment += f" (Кандидати: {', '.join(art.candidates[:2])})"
            elif art.notes and "Wikidata" in art.notes:
                comment += f" ({res.wikidata_id})"
            print(f"{lang.upper():<6} | {status:<10} | {title:<30} | {comment}")
        print("=" * 90 + "\n")


if __name__ == "__main__":
    main()
