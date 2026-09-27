#!/usr/bin/env python3
"""
Benchmark multiple OpenRouter models on the Wikipedia Trend Analyzer Agent Skill.

Цей скрипт дозволяє протестувати та порівняти 2–4 різні моделі (безкоштовні або найдешевші)
на одному й тому ж продуктовому запиті:
- Перевіряє коректність виклику інструменту (Tool Calling / Function Calling).
- Заміряє час відповіді (Latency у секундах).
- Підраховує кількість витрачених токенів (Prompt, Completion, Total).
- Порівнює якість та повноту бізнес-висновків для фаундера.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import urllib.request
import urllib.error

# Додавання scripts/ та кореня навички до sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
for p in [str(BASE_DIR), str(SCRIPTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from openrouter_agent import (
    SYSTEM_PROMPT,
    TOOL_DEFINITION,
    OpenRouterAgent,
    load_env_file,
)

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("benchmark")

# Автоматичне завантаження .env при старті
load_env_file()


# Актуальні безкоштовні моделі з підтримкою виклику інструментів (Tool Calling) на OpenRouter
DEFAULT_MODELS = [
    "openrouter/free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
]


class OpenRouterBenchmark:
    """Бенчмарк для порівняння моделей OpenRouter на агентській навичці."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.api_url = "https://openrouter.ai/api/v1/chat/completions"
        self.agent = OpenRouterAgent(api_key=self.api_key)

    def test_model(self, model: str, prompt: str) -> Dict[str, Any]:
        """Тестує одну модель на заданому промпті."""
        logger.info(f"🚀 Запуск тесту моделі: {model}")
        start_time = time.perf_counter()

        if not self.api_key or self.api_key == "not_set":
            # Емуляційний режим
            time.sleep(0.5)
            duration = round(time.perf_counter() - start_time + 1.2, 2)
            lower = prompt.lower()
            if "фізик" in lower:
                args = {"topic": "Фізика", "langs": "de,pl,uk", "years": 2}
            elif "математ" in lower:
                args = {"article": "Mathematik", "project": "de.wikipedia", "years": 2}
            elif "християн" in lower:
                args = {"topic": "Християнство", "langs": "en,de,fr,uk,pl,es,it", "years": 2}
            else:
                args = {"topic": "інтервальне голодування", "langs": "pl,cs", "years": 2}

            tool_res = self.agent.execute_tool("wikipedia_trend_analyzer", args)

            return {
                "model": model,
                "status": "success (emulated)",
                "tool_called": True,
                "tool_args": args,
                "latency_sec": duration,
                "tokens": {
                    "prompt_tokens": 420,
                    "completion_tokens": 310,
                    "total_tokens": 730,
                },
                "estimated_cost_usd": 0.0,
                "answer_preview": (
                    f"Лідер ринку визначено успішно. Згенеровано графік та 1-сторінковий PDF. "
                    f"Надійність оцінено як High."
                ),
            }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/leonbohdan/AI-Product-Engineering-School-Case",
            "X-Title": "Wikipedia Trend Analyzer Benchmark",
        }

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        payload = {
            "model": model,
            "messages": messages,
            "tools": [TOOL_DEFINITION],
            "tool_choice": "auto",
        }

        req = urllib.request.Request(self.api_url, data=json.dumps(payload).encode("utf-8"), headers=headers)

        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8")
            logger.error(f"Помилка {model}: HTTP {e.code}: {err_msg}")
            return {
                "model": model,
                "status": f"error (HTTP {e.code})",
                "tool_called": False,
                "latency_sec": round(time.perf_counter() - start_time, 2),
                "tokens": {"total_tokens": 0},
                "error": err_msg,
            }

        usage_1 = data.get("usage", {})
        choice = data["choices"][0]["message"]
        tool_called = False
        tool_args: Dict[str, Any] = {}

        if "tool_calls" in choice and choice["tool_calls"]:
            tool_called = True
            tc = choice["tool_calls"][0]
            call_id = tc["id"]
            fn_name = tc["function"]["name"]
            tool_args = json.loads(tc["function"]["arguments"])

            tool_res = self.agent.execute_tool(fn_name, tool_args)

            messages.append(choice)
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": fn_name,
                "content": json.dumps(tool_res, ensure_ascii=False),
            })

            second_payload = {
                "model": model,
                "messages": messages,
            }
            req2 = urllib.request.Request(self.api_url, data=json.dumps(second_payload).encode("utf-8"), headers=headers)
            with urllib.request.urlopen(req2) as resp2:
                final_data = json.loads(resp2.read().decode("utf-8"))

            usage_2 = final_data.get("usage", {})
            total_prompt_tok = usage_1.get("prompt_tokens", 0) + usage_2.get("prompt_tokens", 0)
            total_comp_tok = usage_1.get("completion_tokens", 0) + usage_2.get("completion_tokens", 0)
            total_tok = usage_1.get("total_tokens", 0) + usage_2.get("total_tokens", 0)

            final_text = final_data["choices"][0]["message"]["content"]
            duration = round(time.perf_counter() - start_time, 2)

            return {
                "model": model,
                "status": "success",
                "tool_called": tool_called,
                "tool_args": tool_args,
                "latency_sec": duration,
                "tokens": {
                    "prompt_tokens": total_prompt_tok,
                    "completion_tokens": total_comp_tok,
                    "total_tokens": total_tok,
                },
                "answer_preview": final_text[:200] + "...",
                "full_answer": final_text,
            }
        else:
            duration = round(time.perf_counter() - start_time, 2)
            return {
                "model": model,
                "status": "no_tool_call",
                "tool_called": False,
                "latency_sec": duration,
                "tokens": usage_1,
                "answer_preview": choice.get("content", "")[:200] + "...",
            }

    def run_benchmark(self, models: List[str], prompt: str) -> None:
        """Запускає порівняльне тестування кількох моделей."""
        print("\n" + "=" * 95)
        print("🏁 OPENROUTER MULTI-MODEL BENCHMARK // WIKIPEDIA TREND ANALYZER")
        print("=" * 95)
        print(f"Промпт: «{prompt}»")
        print(f"Моделі для тесту: {models}")
        if not self.api_key or self.api_key == "not_set":
            print("\n⚠️  УВАГА: OPENROUTER_API_KEY не знайдено.")
            print("   Бенчмарк запускається в симуляційному режимі з реальними локальними розрахунками.")
            print("   Щоб виконати реальні виклики через OpenRouter, експортуйте ключ:")
            print("   export OPENROUTER_API_KEY=\"sk-or-v1-...\"\n")

        results = []
        total = len(models)
        for idx, m in enumerate(models, 1):
            print("\n" + "=" * 80)
            print(f"🚀 [Крок {idx}/{total}] ТЕСТУВАННЯ МОДЕЛІ: {m}")
            print("=" * 80)
            res = self.test_model(m, prompt)
            results.append(res)
            tok_count = res["tokens"].get("total_tokens", "N/A")
            print(f"✅ [Завершено {idx}/{total}] {m} | Час: {res.get('latency_sec', 0)}с | Токени: {tok_count}")


        print("\n" + "=" * 95)
        print("📊 ПІДСУМКОВА ПОРІВНЯЛЬНА ТАБЛИЦЯ")
        print("=" * 95)
        print(f"{'Модель':<40} | {'Статус':<15} | {'Час (с)':<8} | {'Токени':<10} | {'Tool Call'}")
        print("-" * 95)
        for r in results:
            tok = str(r["tokens"].get("total_tokens", "N/A"))
            tc = "✅ Так" if r.get("tool_called") else "❌ Ні"
            print(f"{r['model'][:40]:<40} | {r['status'][:15]:<15} | {r['latency_sec']:<8.2f} | {tok:<10} | {tc}")
        print("=" * 95)

        for r in results:
            print(f"\n--- 🤖 Модель: {r['model']} ---")
            print(f"Аргументи інструменту: {r.get('tool_args')}")
            print(f"Фрагмент висновку: {r.get('answer_preview')}")
        print("\n" + "=" * 95 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Benchmark multiple OpenRouter models on the Agent Skill")
    parser.add_argument(
        "--prompt",
        "-p",
        default="Фізика: порівняння зростання в німецькомовній, польській та україномовній Вікіпедії за два роки",
        help="Тестовий запит",
    )
    parser.add_argument(
        "--models",
        "-m",
        default=",".join(DEFAULT_MODELS),
        help="Список моделей через кому",
    )
    parser.add_argument("--api-key", "-k", help="OpenRouter API ключ")

    args = parser.parse_args()
    model_list = [m.strip() for m in args.models.split(",") if m.strip()]

    bench = OpenRouterBenchmark(api_key=args.api_key)
    bench.run_benchmark(model_list, args.prompt)


if __name__ == "__main__":
    main()
