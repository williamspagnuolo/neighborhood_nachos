FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY app/requirements.txt /app/app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/app/requirements.txt

COPY . /app

# Cloud Run provides PORT at runtime.
CMD ["sh", "-c", "gunicorn --bind :${PORT:-8080} --workers 2 --threads 4 --timeout 0 app.app:server"]
