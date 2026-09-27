# Architecture Decision Records (ADRs)

Цей каталог містить зафіксовані архітектурні рішення (Architecture Decision Records) для проєкту **Wikipedia Trend Analysis Agent Skill** (кейс для Genesis AI Product Engineering School).

Кожен документ фіксує контекст проблеми, критерії вибору, розглянуті альтернативи, фінальне рішення та його наслідки (trade-offs).

---

## 📋 Реєстр архітектурних рішень

| ID | Назва рішення | Статус | Дата |
| :---: | :--- | :---: | :---: |
| [ADR-0001](./0001-agent-skill-cli-architecture.md) | **Архітектура Agent Skill: детермінований CLI-оркестратор проти динамічної кодогенерації** | `Accepted` | 2026-09-26 |
| [ADR-0002](./0002-cross-language-entity-resolution.md) | **Стратегія крос-мовного зіставлення тем (Cross-lingual Entity Resolution)** | `Accepted` | 2026-09-26 |
| [ADR-0003](./0003-trend-metrics-and-reliability-score.md) | **Алгоритм аналізу трендів: фільтрація новинних спалахів та метрика довіри (Reliability Score)** | `Accepted` | 2026-09-26 |
| [ADR-0004](./0004-pdf-report-generation-stack.md) | **Стек візуалізації та генерації односторінкового PDF-звіту** | `Accepted` | 2026-09-26 |
| [ADR-0005](./0005-skill-specification-and-lightweight-llm-integration.md) | **Стандартизація Agent Skill та оптимізація для швидких моделей (Claude Haiku 4.5)** | `Accepted` | 2026-09-27 |

---

## 📐 Формат записів
Всі ADR оформлені за адаптованим стандартом **MADR (Markdown Architectural Decision Records)**:
1. **Title & Status**
2. **Context & Problem Statement** (Контекст і постановка проблеми)
3. **Decision Drivers** (Критерії та фактори ухвалення рішення)
4. **Considered Options** (Розглянуті альтернативи з плюсами/мінусами)
5. **Decision Outcome** (Обране рішення та його обґрунтування)
6. **Consequences & Trade-offs** (Позитивні та негативні наслідки, обмеження)
