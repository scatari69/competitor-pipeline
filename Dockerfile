FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Результати пишуться сюди; у compose тека монтується з хоста
RUN mkdir -p /app/results

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/health', timeout=4)"

# --workers 1 обов'язково: active_jobs і черги SSE живуть у пам'яті процесу,
#   з кількома воркерами запит на /api/stream потрапляв би не в той процес.
# --timeout 0 вимикає вбивство воркера: SSE-з'єднання висить весь аналіз,
#   а на CPU він триває хвилини.
CMD ["gunicorn", "--workers", "1", "--threads", "16", "--timeout", "0", \
     "--bind", "0.0.0.0:5000", "--access-logfile", "-", "server:app"]
