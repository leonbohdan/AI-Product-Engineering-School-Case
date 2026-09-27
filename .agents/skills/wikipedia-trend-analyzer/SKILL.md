---
name: wikipedia-trend-analyzer
description: >-
  Workflows, conventions, and engineering standards for developing, testing, and running the Wikipedia Trend Analysis Agent Skill for the Genesis AI Product Engineering School Case. Use when working on the Wikimedia pageviews trend analysis tools, cross-language entity resolution, spike detection, chart generation, PDF reporting, or validating against the case requirements.
---

# Wikipedia Trend Analysis Agent Skill Guide

Цей скіл містить інженерні стандарти, контекст та інструкції для розробки, налагодження та тестування навички аналізу трендів Wikipedia в рамках кейсу **Genesis AI Product Engineering School**.

---

## 1. Контекст кейсу та продуктова мета

- **Аудиторія інструменту:** Засновники B2C-застосунків (EdTech, Health & Fitness, мовні додатки), CPO, Product Managers.
- **Цільова задача:** Валідація інтересу аудиторії перед інвестиціями в нові курси, тематичні напрями або локалізацію в нових мовних регіонах.
- **Формат:** Автономний [Agent Skill](https://agentskills.io/specification) із детермінованим кодом на Python, який агент викликає через CLI, отримуючи компактний JSON-контракт та готові візуальні артефакти (PNG + 1-сторінковий PDF).

---

## 2. Архітектурні домовленості (ADR Reference)

Під час будь-якої розробки необхідно суворо дотримуватися прийнятих рішень:

1. **[ADR-0001: CLI-оркестратор проти кодогенерації](../../../docs/adr/0001-agent-skill-cli-architecture.md):**
   - Усі обчислення, фільтрація та рендеринг виконуються локальним Python-кодом навички.
   - LLM (особливо легкі моделі класу **Claude Haiku 4.5**) отримує компактні агреговані метрики замість сирого масиву 700+ днів.
2. **[ADR-0002: Крос-мовне зіставлення через Wikidata](../../../docs/adr/0002-cross-language-entity-resolution.md):**
   - Використовувати Wikipedia OpenSearch + Wikidata API (`wbgetentities` / `sitelinks`) для автоматичного пошуку точних назв статей різними мовами (`uk`, `pl`, `cs`, `en` тощо) без помилок 404.
3. **[ADR-0003: Аналіз трендів, фільтрація спалахів та Reliability Score](../../../docs/adr/0003-trend-metrics-and-reliability-score.md):**
   - Фільтрація ботів (`agent=user`).
   - 30-денне ковзне середнє для усунення шуму та вихідних днів.
   - Детекція аномальних спалахів новинного шуму ($Q3 + 1.5 \times \text{IQR}$).
   - Розрахунок `Reliability Score` (0–100%) на основі обсягу вибірки, дисперсії та частки спайків.
4. **[ADR-0004: Стек візуалізації та PDF (Matplotlib + ReportLab)](../../../docs/adr/0004-pdf-report-generation-stack.md):**
   - Безвіконний рендеринг графіків через `matplotlib` (`backend: Agg`, 300 DPI).
   - Жорстка верстка 1-сторінкового звіту A4 через `ReportLab Platypus` без зовнішніх важких залежностей на кшталт Chromium.

---

## 3. Очікувана структура коду навички

Навичка розташовується в `skills/wikipedia-trend-analyzer/`:

```text
skills/wikipedia-trend-analyzer/
├── SKILL.md                 # Системний опис навички для сторонніх LLM-агентів
├── requirements.txt         # Відтворювані залежності (requests, pandas, matplotlib, reportlab)
├── run.py                   # Головний CLI-оркестратор (точка входу)
├── scripts/
│   ├── wikimedia_client.py  # Клієнт Wikimedia REST API (обробка rate-limits, User-Agent)
│   ├── topic_resolver.py    # Резолв сутностей та назв статей різними мовами через Wikidata
│   ├── trend_analyzer.py    # Математика: YoY, 30d MA, IQR спайки, Reliability Score
│   ├── chart_generator.py   # Побудова графіків (PNG, 300 DPI)
│   └── pdf_generator.py     # Збірка 1-сторінкового PDF-звіту (ReportLab)
├── tests/
│   ├── test_client.py       # Тести API та моків
│   ├── test_analyzer.py     # Тести математики та детекції аномалій
│   └── test_cases.py        # E2E валідація 3 цільових запитів
└── examples/                # Зразки згенерованих звітів
```

---

## 4. Специфікація API та інтеграцій

### 4.1. Wikimedia Analytics REST API
- **Endpoint:**
  `https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/{access}/{agent}/{article}/{granularity}/{start}/{end}`
- **Параметри:**
  - `project`: `<lang>.wikipedia.org` (наприклад, `pl.wikipedia.org`, `cs.wikipedia.org`)
  - `access`: `all-access`
  - `agent`: **`user`** (критично для відсікання ботів і скрейперів)
  - `granularity`: `daily`
  - `start` / `end`: формат `YYYYMMDD00`
- **Заголовки:** Обов'язковий `User-Agent: WikipediaTrendAnalyzer/1.0 (contact: genesis-case@example.com)` згідно з політикою Wikimedia API.

### 4.2. Wikidata Entity Resolution API
- **Крок 1 (Search):** `https://{lang}.wikipedia.org/w/api.php?action=opensearch&search={query}&limit=1&format=json`
- **Крок 2 (Pageprops/Wikidata ID):** `https://{lang}.wikipedia.org/w/api.php?action=query&prop=pageprops&titles={title}&format=json` -> отримуємо `wikibase_item` (наприклад, `Q3058848`).
- **Крок 3 (Sitelinks):** `https://www.wikidata.org/w/api.php?action=wbgetentities&ids={QID}&props=sitelinks&format=json` -> отримуємо точні назви статей для `plwiki`, `cswiki`, `ukwiki` тощо.

---

## 5. Контракт виклику CLI (`run.py`)

Агент викликає навичку через термінал:

```bash
python skills/wikipedia-trend-analyzer/run.py \
  --topic "Інтервальне голодування" \
  --langs "pl,cs" \
  --years 2 \
  --pdf
```

### Формат поверненого JSON (stdout):
```json
{
  "status": "success",
  "topic": "Інтервальне голодування",
  "period": { "start": "2024-09-01", "end": "2026-09-01" },
  "languages": {
    "pl": {
      "article": "Post_przerywany",
      "total_views": 482000,
      "yoy_growth_percent": 34.2,
      "spike_impact_percent": 8.1,
      "reliability_score": 86,
      "confidence": "High"
    },
    "cs": {
      "article": "Přerušovaný_půst",
      "total_views": 154000,
      "yoy_growth_percent": 14.5,
      "spike_impact_percent": 11.4,
      "reliability_score": 72,
      "confidence": "Moderate"
    }
  },
  "chart_path": "output/charts/trend_intermittent_fasting_pl_cs.png",
  "pdf_path": "output/reports/report_intermittent_fasting_pl_cs.pdf",
  "key_findings": [
    "Польський ринок демонструє втричі більший обсяг інтересу та вдвічі вищі темпи зростання YoY (+34.2% проти +14.5%).",
    "Рівень довіри до даних польського розділу високий (86/100), зростання є стійким органічним трендом без критичних новинних аномалій."
  ]
}
```

---

## 6. Валідація на тестових сценаріях (Критерії готовності)

Перед фіналізацією навичка повинна успішно відпрацьовувати три запити із завдання (`docs/task/pes_task_1.md`):

1. **Тест 1 (PL vs CS - 2 роки):**  
   *«Порівняй зростання інтересу до інтервального голодування в польськомовній та чеськомовній Wikipedia за останні два роки.»*  
   -> Перевірка коректності резолву `Post_przerywany` і `Přerušovaný_půst`, порівняння YoY та відносних обсягів.
2. **Тест 2 (UK - надійність тренду):**  
   *«Ми думаємо додати курс з астрономії до освітнього застосунку. Чи зростає інтерес до цієї теми в україномовній Wikipedia, і наскільки цьому зростанню можна довіряти?»*  
   -> Розрахунок `Reliability Score`, виявлення сонячних затемнень/запусків як новинних спайків, чіткий висновок щодо довіри.
3. **Тест 3 (Вивчення мов - пріоритетизація):**  
   *«Ми створюємо застосунок для вивчення мов. Порівняй інтерес до вивчення англійської у вибраних мовних розділах та підготуй короткий звіт: які аудиторії варто дослідити наступними й чому?»*  
   -> Порівняння кількох мовних розділів (наприклад, `es`, `pl`, `uk`, `de`), оцінка потенціалу аудиторій та генерація 1-сторінкового PDF-звіту.
