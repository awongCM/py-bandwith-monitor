FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system monitor \
    && useradd --system --gid monitor --home-dir /app --shell /usr/sbin/nologin monitor \
    && mkdir -p /data \
    && chown monitor:monitor /data

COPY requirements.txt .
# Prefer pinned lockfile when present (from config/polish branch after merge).
COPY requirements.loc[k] ./
RUN if [ -f requirements.lock ]; then \
      pip install --no-cache-dir -r requirements.lock; \
    else \
      pip install --no-cache-dir -r requirements.txt; \
    fi

COPY monitor/ monitor/
COPY main.py .
COPY config.example.yam[l] ./

RUN chown -R monitor:monitor /app

USER monitor

EXPOSE 8080

VOLUME ["/data"]

# Mount your config at /data/config.yaml (copy from config.example.yaml).
# CLI flags override YAML; host/port/db defaults here suit containers.
# Missing config file falls back to built-in defaults.
CMD [
    "python", "-m", "monitor", "serve",
    "--config", "/data/config.yaml",
    "--host", "0.0.0.0",
    "--port", "8080",
    "--db", "/data/monitor.db"
]
