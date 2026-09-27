#!/usr/bin/env python3
"""
OpenRouter Agent Runner for Wikipedia Trend Analyzer.

Цей скрипт демонструє повний цикл роботи AI-агента на базі OpenRouter
(використовуючи безкоштовні або недорогі моделі класу Claude Haiku 4.5 / Gemini Flash):
1. Отримує промпт від користувача.
2. Підключає інструмент `wikipedia_trend_analyzer` через стандартний OpenAI-сумісний Tools API OpenRouter.
3. Отримує від LLM рішення викликати інструмент (Tool Call).
4. Виконує SkillOrchestrator локально і повертає компактний JSON-контракт.
5. Передає результат моделі для генерації фінального бізнес-звіту та рекомендацій.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
import urllib.request
import urllib.error

# Додавання scripts/ та кореня навички до sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent.parent
for p in [str(BASE_DIR), str(SCRIPTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from run import SkillOrchestrator

logger = logging.getLogger("openrouter_agent")
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def load_env_file() -> None:
    """
    Автоматично шукає та завантажує .env файл із кореня репозиторію,
    поточного робочого каталогу або каталогу навички.
    """
    candidates = [
        Path.cwd() / ".env",
        REPO_ROOT / ".env",
        BASE_DIR / ".env",
    ]
    # Спроба через python-dotenv
    try:
        from dotenv import load_dotenv
        for c in candidates:
            if c.is_file():
                load_dotenv(c)
                logger.info(f"🔑 Завантажено змінні середовища з: {c}")
                return
    except ImportError:
        pass

    # Автономний fallback
    for c in candidates:
        if c.is_file():
            try:
                with open(c, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("'\"")
                            if k and k not in os.environ:
                                os.environ[k] = v
                logger.info(f"🔑 Завантажено змінні середовища (standalone) з: {c}")
                return
            except Exception:
                pass


# Автоматичне завантаження .env при старті
load_env_file()


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "wikipedia_trend_analyzer",
        "description": (
            "Аналізує ринковий попит та тренди інтересу до тем на основі статистики переглядів "
            "Вікіпедії (Wikimedia Pageviews). Фільтрує ботів, рахує YoY ріст, виявляє новинні сплески "
            "та обчислює 0-100% Reliability Score. Генерує графік і 1-сторінковий PDF."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Тема дослідження будь-якою мовою (наприклад 'Фізика', 'Християнство', 'інтервальне голодування')",
                },
                "article": {
                    "type": "string",
                    "description": "Точна назва статті у Вікіпедії (якщо досліджується одна конкретна стаття)",
                },
                "project": {
                    "type": "string",
                    "description": "Мовний проєкт для дослідження однієї статті (наприклад 'de.wikipedia', 'uk.wikipedia')",
                },
                "langs": {
                    "type": "string",
                    "description": "Список мов через кому для крос-мовного аналізу (наприклад 'de,pl,uk' або 'en,de,fr,uk,pl,es,it')",
                },
                "years": {
                    "type": "integer",
                    "description": "Кількість років аналізу (за замовчуванням 2)",
                },
                "override": {
                    "type": "string",
                    "description": "Ручне перевизначення назв для конкретних мов (наприклад 'pl:Głodówka_lecznicza')",
                },
            },
            "required": [],
        },
    },
}

SYSTEM_PROMPT = """Ти — аналітичний AI-асистент для засновників B2C-продуктів (CPO, Product Managers).
Твоє завдання — допомагати ухвалювати рішення щодо вибору нових тем, курсів або локалізації продуктів новими мовами на основі даних Вікіпедії.

ТИ МАЄШ ДОСТУП ДО ІНСТРУМЕНТУ `wikipedia_trend_analyzer`.
Завжди використовуй цей інструмент, коли користувач запитує про інтерес, тренди чи порівняння ринків.

Правила інтерпретації результатів інструменту:
1. Reliability Score (0-100%):
   - 80-100% [High]: тренд надійний, вибірка велика, можна використовувати для планування MVP.
   - 50-79% [Moderate]: помірний попит або є сезонність/новинні сплески. Потрібна додаткова валідація.
   - 0-49% [Low]: низька достовірність через малий обсяг трафіку або одноразові спалахи.
2. Динаміка YoY vs Organic YoY:
   - Якщо загальний YoY відрізняється від Organic YoY, вкажи, що різницю спричинили новинні сплески.
3. Продуктові висновки:
   - Визнач ринок-лідер за обсягом і ринок із найкращою динамікою.
   - Поясни обмеження Вікіпедії (когнітивний інтерес не дорівнює готовності платити).
   - Запропонуй 2 наступні кроки для команди (Google Search Volume, Smoke Test).
"""


class OpenRouterAgent:
    """Агент, що взаємодіє з OpenRouter API та виконує інструменти навички."""

    def __init__(self, api_key: Optional[str] = None, model: str = "google/gemini-2.0-flash-exp:free"):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.model = model
        self.orchestrator = SkillOrchestrator()
        self.api_url = "https://openrouter.ai/api/v1/chat/completions"

    def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Виконує інструмент локально."""
        logger.info(f"🛠 Виклик інструменту '{tool_name}' з аргументами: {arguments}")
        if tool_name != "wikipedia_trend_analyzer":
            return {"error": f"Невідомий інструмент: {tool_name}"}

        topic = arguments.get("topic")
        article = arguments.get("article")
        project = arguments.get("project", "uk.wikipedia")
        langs_str = arguments.get("langs")
        years = arguments.get("years", 2)
        override = arguments.get("override")

        from topic_resolver import parse_override_arg

        if langs_str and (topic or article):
            target_langs = [l.strip() for l in langs_str.split(",") if l.strip()]
            overrides_dict = parse_override_arg(override) if override else None
            return self.orchestrator.run_multi(
                topic=topic or article,
                langs=target_langs,
                years=years,
                overrides=overrides_dict,
                generate_pdf=True,
                generate_chart=True,
            )
        elif article or topic:
            art = article or topic
            return self.orchestrator.run_single(
                article=art,
                project=project,
                years=years,
                generate_pdf=True,
                generate_chart=True,
            )
        else:
            return {"error": "Потрібно вказати topic або article"}

    def run(self, user_prompt: str) -> str:
        """Запускає повний діалоговий цикл агента."""
        print("\n" + "=" * 80)
        print(f"🤖 АКТИВНА МОДЕЛЬ: {self.model}")
        print(f"👤 ЗАПИТ КОРИСТУВАЧА:\n«{user_prompt}»")
        print("=" * 80)

        # Якщо ключа немає — виконуємо симуляцію з реальним виконанням інструменту
        if not self.api_key or self.api_key == "not_set":
            return self._simulate_agent_flow(user_prompt)

        return self._call_openrouter_api(user_prompt)

    def _call_openrouter_api(self, user_prompt: str) -> str:
        """Реальний виклик OpenRouter API з підтримкою паралельних та послідовних викликів інструментів."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/leonbohdan/AI-Product-Engineering-School-Case",
            "X-Title": "Wikipedia Trend Analyzer Agent",
        }

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        max_turns = 5
        turn = 0
        total_tok = 0
        p_tok = 0
        c_tok = 0

        while turn < max_turns:
            turn += 1
            payload = {
                "model": self.model,
                "messages": messages,
                "tools": [TOOL_DEFINITION],
                "tool_choice": "auto",
            }

            logger.info(f"Відправка запиту до OpenRouter (модель: {self.model}, крок {turn})...")
            req = urllib.request.Request(self.api_url, data=json.dumps(payload).encode("utf-8"), headers=headers)

            try:
                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                err_msg = e.read().decode("utf-8")
                logger.error(f"Помилка OpenRouter API: HTTP {e.code}: {err_msg}")
                return f"Помилка OpenRouter API: {err_msg}"

            usage = data.get("usage", {})
            total_tok += usage.get("total_tokens", 0)
            p_tok += usage.get("prompt_tokens", 0)
            c_tok += usage.get("completion_tokens", 0)

            choice = data["choices"][0]["message"]
            messages.append(choice)

            tool_calls = choice.get("tool_calls")
            if not tool_calls:
                # Модель сформувала фінальну текстову відповідь
                final_text = choice.get("content", "Немає відповіді від моделі.")
                header = (
                    f"\n{'=' * 80}\n"
                    f"🤖 ВІДПОВІДЬ ЗГЕНЕРОВАНО МОДЕЛЛЮ: {self.model}\n"
                    f"📊 Статистика: всього токенів: {total_tok} (Prompt: {p_tok}, Completion: {c_tok})\n"
                    f"{'=' * 80}\n\n"
                )
                return header + final_text

            # Обробка та послідовне виконання всіх викликів інструментів поточної ітерації
            logger.info(f"Модель ініціювала {len(tool_calls)} виклик(ів) інструментів:")
            for tc in tool_calls:
                call_id = tc.get("id") or f"call_{turn}_{len(messages)}"
                fn_name = tc.get("function", {}).get("name", "")
                raw_args = tc.get("function", {}).get("arguments", "{}")
                try:
                    fn_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception as e:
                    logger.warning(f"Помилка парсингу аргументів інструменту {raw_args}: {e}")
                    fn_args = {}

                tool_result = self.execute_tool(fn_name, fn_args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": fn_name,
                    "content": json.dumps(tool_result, ensure_ascii=False),
                })

        return "Досягнуто ліміт ітерацій виклику інструментів (max_turns=5)."

    def _simulate_agent_flow(self, user_prompt: str) -> str:
        """
        Демонстраційний режим без зовнішнього ключа:
        1. Демонструє, як модель витягує параметри з промпту.
        2. Виконує реальний інструмент та формує артефакти.
        3. Генерує продуктовий висновок за системним шаблоном.
        """
        logger.info("OPENROUTER_API_KEY не знайдено. Запуск демонстраційної емуляції агента.")

        lower = user_prompt.lower()
        if "астроном" in lower or "astronom" in lower:
            args = {"article": "Астрономія", "project": "uk.wikipedia", "years": 2}
        elif "англ" in lower or "english" in lower:
            args = {"topic": "English language", "langs": "de,pl,cs,uk,es", "years": 2}
        elif "голодуван" in lower or "fasting" in lower:
            args = {"topic": "інтервальне голодування", "langs": "pl,cs", "years": 2, "override": "pl:Głodówka_lecznicza"}
        elif "фізик" in lower or "physic" in lower:
            args = {"topic": "Фізика", "langs": "de,pl,uk", "years": 2}
        elif "математ" in lower or "math" in lower:
            args = {"article": "Mathematik", "project": "de.wikipedia", "years": 2}
        elif "християн" in lower or "christian" in lower:
            args = {"topic": "Християнство", "langs": "en,de,fr,uk,pl,es,it", "years": 2}
        else:
            args = {"topic": "інтервальне голодування", "langs": "pl,cs", "years": 2}

        # Реальне виконання детермінованого коду навички
        result = self.execute_tool("wikipedia_trend_analyzer", args)

        print("\n📦 КОМПАКТНИЙ JSON-КОНТРАКТ, ПОВЕРНЕНИЙ МОДЕЛІ (менше 600 токенів):")
        print(json.dumps(result, ensure_ascii=False, indent=2))

        print("\n🤖 ВІДПОВІДЬ AI-АГЕНТА ДЛЯ ФАУНДЕРА:")
        if result["mode"] == "single_article":
            m = result["metrics"]
            r = result["reliability"]
            reply = (
                f"### Аналітичний висновок щодо теми «{result['topic']}» ({result['project']})\n\n"
                f"1. **Обсяг та стабільність попиту:**\n"
                f"   - Сумарні перегляди за 2 роки: **{m['total_views']:,}** (в середньому {m['daily_average']:.1f}/день, медіана: {m['daily_median']:.1f}).\n"
                f"   - Довгострокова динаміка: **YoY {m['yoy_growth_percent']:+.1f}%** (органічний бейзлайн: {m['organic_yoy_percent']}%), статус: `{m['trend_direction']}`.\n\n"
                f"2. **Оцінка надійності (Reliability Score):**\n"
                f"   - **{r['score']:.0f}/100 [{r['confidence_level']} Confidence]**.\n"
                f"   - {r['verdict']}\n"
                f"   - Частка новинного шуму мінімальна: лише {result['spikes']['spike_impact_percent']:.1f}% трафіку припадає на пікові спалахи.\n\n"
                f"3. **Продуктова рекомендація:**\n"
                f"   - Дані демонструють зрілий, стабільний інтерес аудиторії. Ринок готовий до навчального продукту.\n"
                f"   - Згенеровано 1-сторінковий звіт для команди: `{result['artifacts']['report_pdf']}`\n\n"
                f"4. **Наступні кроки для перевірки (Caveats):**\n"
                f"   - Перевірте комерційні пошукові запити в Google Keyword Planner для німецькомовного регіону (DACH).\n"
                f"   - Запустіть Smoke Test / Landing page для оцінки готовності платити."
            )
        else:
            comp = result["comparison"]
            top_vol = comp["top_market_by_volume"].upper()
            top_rel = comp["top_market_by_reliability"].upper()

            rows = []
            for lang, d in result["languages"].items():
                yoy = f"{d['yoy_growth_percent']:+.1f}%" if d['yoy_growth_percent'] is not None else "N/A"
                rows.append(f"| {lang.upper()} | {d['display_title']} | {d['total_views']:,} | {yoy} | {d['reliability_score']:.0f}/100 ({d['confidence_level']}) |")

            table_md = "\n".join(rows)

            reply = (
                f"### Крос-мовне дослідження ринків: «{result['topic']}»\n\n"
                f"| Ринок | Стаття | Перегляди (2 роки) | YoY ріст | Надійність |\n"
                f"| :--- | :--- | :--- | :--- | :--- |\n"
                f"{table_md}\n\n"
                f"1. **Ключові ринкові сигнали:**\n"
                f"   - **Лідер за обсягом:** **{top_vol}** ({comp['volume_shares_percent'].get(top_vol.lower(), 0):.1f}% від сукупного попиту).\n"
                f"   - **Найбільш надійний ринок:** **{top_rel}**.\n\n"
                f"2. **Пріоритетизація локалізації:**\n"
                f"   - {result['executive_summary']}\n"
                f"   - Рекомендується починати розгортання саме з топового ринку, оскільки він має сформовану критичну масу органічного інтересу.\n\n"
                f"3. **Артефакти для стейкхолдерів:**\n"
                f"   - Візуальний графік: `{result['artifacts']['chart_png']}`\n"
                f"   - Односторінковий звіт: `{result['artifacts']['report_pdf']}`\n\n"
                f"4. **Тріангуляція гіпотез:**\n"
                f"   - Оцініть пошуковий обсяг у локальних Google пошуковиках.\n"
                f"   - Проведіть бенчмарк конкурентів у локальних App Store / Google Play."
            )

        return reply


def main():
    parser = argparse.ArgumentParser(description="OpenRouter Agent Runner for Wikipedia Trend Analyzer")
    parser.add_argument("--prompt", "-p", required=True, help="Промпт або запит користувача до агента")
    parser.add_argument("--api-key", "-k", help="OpenRouter API Key (або через змінну OPENROUTER_API_KEY)")
    parser.add_argument("--model", "-m", default="google/gemini-2.0-flash-exp:free", help="Назва моделі на OpenRouter")

    args = parser.parse_args()
    agent = OpenRouterAgent(api_key=args.api_key, model=args.model)
    response = agent.run(args.prompt)
    print("\n" + response + "\n")


if __name__ == "__main__":
    main()
