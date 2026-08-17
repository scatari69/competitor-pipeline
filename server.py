"""
Flask API — бекенд для дашборду
Підтримує SSE (Server-Sent Events) для real-time прогресу
"""
import json
import threading
import queue
import logging
import sys
import os
from pathlib import Path
from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("pipeline.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# Active pipelines: job_id -> {"status": ..., "queue": Queue}
active_jobs: dict[str, dict] = {}
job_counter = 0


def generate_job_id():
    global job_counter
    job_counter += 1
    return f"job_{job_counter}"


# ─── API Routes ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.route("/api/analyze", methods=["POST"])
def start_analysis():
    """Запускає pipeline в окремому треді, повертає job_id."""
    data = request.json or {}
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "url is required"}), 400

    job_id = generate_job_id()
    q = queue.Queue()
    active_jobs[job_id] = {"status": "running", "queue": q, "result": None}

    def run():
        from pipeline import run_pipeline

        def on_progress(status_dict):
            q.put({"type": "progress", "data": status_dict})

        try:
            result = run_pipeline(url, on_progress=on_progress)
            active_jobs[job_id]["result"] = result.to_dict()
            active_jobs[job_id]["status"] = "done"
            q.put({"type": "done", "data": result.to_dict()})
        except Exception as e:
            log.error(f"Pipeline error: {e}")
            active_jobs[job_id]["status"] = "error"
            q.put({"type": "error", "data": {"message": str(e)}})

    t = threading.Thread(target=run, daemon=True)
    t.start()

    return jsonify({"job_id": job_id, "status": "started"})


@app.route("/api/stream/<job_id>")
def stream_progress(job_id: str):
    """SSE endpoint — стрімить прогрес pipeline."""
    if job_id not in active_jobs:
        return jsonify({"error": "job not found"}), 404

    def generate():
        q = active_jobs[job_id]["queue"]
        yield "data: {\"type\": \"connected\"}\n\n"

        while True:
            try:
                event = q.get(timeout=30)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] in ("done", "error"):
                    break
            except queue.Empty:
                yield "data: {\"type\": \"ping\"}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/results")
def get_results():
    """Повертає список збережених аналізів."""
    from pipeline import load_saved_results
    try:
        results = load_saved_results()
        summaries = []
        for r in results:
            analysis = r.get("result", {}).get("analysis", {})
            summaries.append({
                "filename": r.get("_filename"),
                "company_name": r.get("company_name", "?"),
                "url": r.get("competitor_url"),
                "finished_at": r.get("finished_at"),
                "threat_level": analysis.get("threat_level"),
                "summary": analysis.get("summary"),
                "pricing": analysis.get("pricing", {}),
                "strengths_count": len(analysis.get("strengths", [])),
                "weaknesses_count": len(analysis.get("weaknesses", [])),
            })
        return jsonify(summaries)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/results/<filename>")
def get_result_detail(filename: str):
    """Повертає повний результат конкретного аналізу."""
    from pipeline import RESULTS_DIR
    try:
        path = RESULTS_DIR / filename
        if not path.exists():
            return jsonify({"error": "not found"}), 404
        with open(path, encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/compare", methods=["POST"])
def compare_competitors():
    """Генерує порівняльну матрицю для обраних аналізів."""
    from analyzers.ai_analyzer import generate_comparison_matrix
    from pipeline import load_saved_results, RESULTS_DIR
    import json

    data = request.json or {}
    filenames = data.get("filenames", [])

    analyses = []
    for fn in filenames:
        path = RESULTS_DIR / fn
        if path.exists():
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
                analysis = saved.get("result", {}).get("analysis", {})
                if analysis:
                    analyses.append(analysis)

    if len(analyses) < 2:
        return jsonify({"error": "Потрібно щонайменше 2 аналізи для порівняння"}), 400

    matrix = generate_comparison_matrix(analyses)
    return jsonify(matrix)


@app.route("/api/health")
def health():
    """Стан сервера і локальної моделі."""
    from analyzers.llm_client import get_client

    llm = get_client().health()
    ready = llm["reachable"] and llm["model_available"]
    return jsonify({
        "status": "ok",
        "llm_ready": ready,
        "llm_model": llm["model"],
        "llm_base_url": llm["base_url"],
        "llm_error": llm["error"],
        "active_jobs": len(active_jobs),
    })


if __name__ == "__main__":
    from analyzers.llm_client import get_client

    port = int(os.environ.get("PORT", 5000))
    log.info(f"Starting Competitor Analysis Pipeline on http://localhost:{port}")

    llm_health = get_client().health()
    if llm_health["model_available"]:
        log.info(f"LLM: ✓ {llm_health['model']} @ {llm_health['base_url']}")
    else:
        log.warning(f"LLM: ✗ {llm_health['error']} ({llm_health['base_url']})")
    app.run(debug=False, host="0.0.0.0", port=port, threaded=True)
