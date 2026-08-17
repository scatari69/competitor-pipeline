"""
Pipeline orchestrator — координує всі етапи аналізу
"""
import json
import time
import logging
import os
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable

log = logging.getLogger(__name__)

# Output directory for results
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


@dataclass
class PipelineStatus:
    competitor_url: str
    company_name: str = ""
    started_at: str = ""
    finished_at: str = ""
    current_stage: str = "idle"
    current_stage_idx: int = 0
    stages_completed: list = field(default_factory=list)
    stages_log: list = field(default_factory=list)
    result: dict = field(default_factory=dict)
    error: str = ""

    STAGES = [
        "scraping_website",
        "scraping_socials",
        "scraping_reviews",
        "ai_analysis",
        "saving_results",
        "done",
    ]

    def log_event(self, msg: str, level: str = "info"):
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "stage": self.current_stage,
            "level": level,
            "message": msg,
        }
        self.stages_log.append(entry)
        getattr(log, level)(msg)

    def advance(self, stage_name: str):
        if self.current_stage and self.current_stage != "idle":
            self.stages_completed.append(self.current_stage)
        self.current_stage = stage_name
        self.current_stage_idx = self.STAGES.index(stage_name) if stage_name in self.STAGES else 0

    def to_dict(self):
        return asdict(self)


def run_pipeline(
    url: str,
    on_progress: Optional[Callable] = None,
) -> PipelineStatus:
    """
    Запускає повний pipeline аналізу конкурента.

    Args:
        url: URL або назва компанії для аналізу
        on_progress: callback(status_dict) — викликається після кожного кроку

    Returns:
        PipelineStatus з результатами
    """
    from scrapers.web_scraper import scrape_website, scrape_reviews
    from scrapers.social_scraper import scrape_social_presence
    from analyzers.ai_analyzer import analyze_competitor

    status = PipelineStatus(
        competitor_url=url,
        started_at=datetime.now().isoformat(),
    )

    def progress(msg, level="info"):
        status.log_event(msg, level)
        if on_progress:
            on_progress(status.to_dict())

    # ── Stage 1: Website scraping ──────────────────────────────────
    status.advance("scraping_website")
    progress(f"Починаємо аналіз: {url}")
    progress("Завантажуємо головну сторінку...")

    try:
        scrape_result = scrape_website(url)
        status.company_name = scrape_result.company_name or url

        if scrape_result.status == "error":
            progress(f"Помилка скрейпінгу: {scrape_result.errors}", "warning")
        else:
            progress(f"Сайт зібрано: знайдено {len(scrape_result.prices)} цін, {len(scrape_result.social_links)} соцмереж")
            if scrape_result.price_pages:
                progress(f"Підсторінки з тарифами: {', '.join(scrape_result.price_pages[:3])}")
    except Exception as e:
        progress(f"Критична помилка при скрейпінгу: {e}", "error")
        status.error = str(e)
        return status

    # ── Stage 2: Social media ──────────────────────────────────────
    status.advance("scraping_socials")
    progress(f"Аналізуємо соцмережі ({len(scrape_result.social_links)} знайдено)...")

    social_data = {}
    if scrape_result.social_links:
        progress(f"Платформи: {', '.join(scrape_result.social_links.keys())}")
        try:
            social_data = scrape_social_presence(scrape_result.social_links)
            active = [p for p, d in social_data.items() if not d.get("error")]
            progress(f"Соцмережі зібрано: {', '.join(active) if active else 'немає даних'}")
        except Exception as e:
            progress(f"Помилка соцмереж: {e}", "warning")
    else:
        progress("Соцмережі не знайдені на сайті")

    # ── Stage 3: Reviews ───────────────────────────────────────────
    status.advance("scraping_reviews")
    company_for_reviews = status.company_name or url
    progress(f"Шукаємо відгуки про: {company_for_reviews}")

    review_snippets = []
    try:
        review_snippets = scrape_reviews(company_for_reviews)
        progress(f"Відгуки: знайдено {len(review_snippets)} фрагментів")
    except Exception as e:
        progress(f"Помилка при пошуку відгуків: {e}", "warning")

    # ── Stage 4: AI Analysis ───────────────────────────────────────
    status.advance("ai_analysis")
    progress("Відправляємо дані до Claude AI для аналізу...")
    progress("Це займає 15-30 секунд...")

    try:
        analysis = analyze_competitor(
            scraped_data=scrape_result.to_dict(),
            social_data=social_data,
            review_snippets=review_snippets,
        )

        if "error" in analysis:
            progress(f"Помилка аналізу AI: {analysis['error']}", "error")
            status.result = {"scrape": scrape_result.to_dict(), "analysis_error": analysis["error"]}
        else:
            progress(f"AI аналіз завершено: загроза — {analysis.get('threat_level', '?')}, "
                     f"сильних сторін — {len(analysis.get('strengths', []))}")
            status.result = {
                "scrape": scrape_result.to_dict(),
                "social": social_data,
                "reviews": review_snippets,
                "analysis": analysis,
            }
            status.company_name = analysis.get("company_name", status.company_name)
    except Exception as e:
        progress(f"Критична помилка AI: {e}", "error")
        status.error = str(e)

    # ── Stage 5: Save ──────────────────────────────────────────────
    status.advance("saving_results")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() else "_" for c in status.company_name[:30])
    filename = RESULTS_DIR / f"{safe_name}_{timestamp}.json"

    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(status.to_dict(), f, ensure_ascii=False, indent=2)
        progress(f"Результати збережено: {filename.name}")
    except Exception as e:
        progress(f"Помилка збереження: {e}", "warning")

    status.advance("done")
    status.finished_at = datetime.now().isoformat()
    duration = datetime.fromisoformat(status.finished_at) - datetime.fromisoformat(status.started_at)
    progress(f"Pipeline завершено за {duration.seconds} секунд")

    if on_progress:
        on_progress(status.to_dict())

    return status


def load_saved_results() -> list[dict]:
    """Завантажує всі збережені результати аналізів."""
    results = []
    for f in sorted(RESULTS_DIR.glob("*.json"), reverse=True):
        try:
            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)
                data["_filename"] = f.name
                results.append(data)
        except Exception:
            pass
    return results


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://lanet.ua"

    def print_progress(status):
        last = status["stages_log"][-1] if status["stages_log"] else {}
        print(f"[{last.get('time','')}] {last.get('stage','')} — {last.get('message','')}")

    result = run_pipeline(url, on_progress=print_progress)
    print("\n=== РЕЗУЛЬТАТ ===")
    analysis = result.result.get("analysis", {})
    print(f"Компанія: {analysis.get('company_name', result.company_name)}")
    print(f"Загроза: {analysis.get('threat_level', '?')}")
    print(f"Висновок: {analysis.get('summary', '—')}")
