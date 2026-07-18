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
RUN pip install --no-cache-dir -r requirements.txt

COPY monitor/ monitor/
COPY main.py .

RUN chown -R monitor:monitor /app

USER monitor

EXPOSE 8080

VOLUME ["/data"]

CMD ["python", "-m", "monitor", "serve", "--host", "0.0.0.0", "--port", "8080", "--db", "/data/monitor.db"]
