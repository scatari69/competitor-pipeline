# Competitor Intelligence Pipeline

AI-інструмент для автоматичного аналізу конкурентів: сайти, тарифи, соцмережі, відгуки.

## Структура проєкту

```
competitor_pipeline/
├── server.py              — Flask API + SSE стрімінг
├── pipeline.py            — Оркестратор всіх етапів
├── scrapers/
│   ├── web_scraper.py     — Скрейпінг сайтів, тарифів, відгуків
│   └── social_scraper.py  — Публічні дані соцмереж
├── analyzers/
│   └── ai_analyzer.py     — Claude AI аналіз і порівняльна матриця
├── templates/
│   └── index.html         — Інтерактивний дашборд
├── results/               — Збережені JSON-результати
└── requirements.txt
```

## Встановлення

```bash
cd competitor_pipeline
pip install -r requirements.txt
```

## Запуск

1. Встановіть API ключ Anthropic:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

2. Запустіть сервер:

```bash
python server.py
```

3. Відкрийте браузер: **http://localhost:5000**

## Використання

### Через дашборд (рекомендовано)

1. Введіть URL конкурента або назву компанії
2. Натисніть «Аналізувати»
3. Спостерігайте за прогресом у real-time
4. Перегляньте повний звіт із тарифами, соцмережами, репутацією
5. Для порівняння — позначте кілька аналізів в «Історії» і натисніть «Порівняти»

### Через командний рядок

```bash
# Аналіз одного конкурента
python pipeline.py https://lanet.ua

# Тест скрейпера
python scrapers/web_scraper.py https://ukrtelecom.ua

# Тест аналізатора
python analyzers/ai_analyzer.py
```

## Що аналізується

| Джерело | Що збирається |
|---------|---------------|
| Сайт | Тексти, навігація, технології, meta |
| Сторінки тарифів | Ціни (UAH), назви пакетів, умови |
| Соцмережі | Facebook, Instagram, Telegram: підписники, контент |
| Відгуки | Фрагменти з Google Search |
| Claude AI | Структурований аналіз, загрози, можливості, рекомендації |

## Обмеження

- **Facebook/Instagram**: сильно обмежені без авторизації — отримуємо базові дані
- **Google Search**: скрейпінг може блокуватися (rate limiting)
- **Telegram**: працює добре для публічних каналів
- **Playwright**: для складних JS-сайтів можна розширити web_scraper.py

## Розширення

Для моніторингу змін (цін, контенту) — запускайте pipeline за розкладом через cron:

```bash
# Щотижневий моніторинг
0 9 * * 1 cd /path/to/competitor_pipeline && python pipeline.py https://competitor.ua
```
