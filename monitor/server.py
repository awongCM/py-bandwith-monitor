"""FastAPI dashboard server with REST and WebSocket endpoints."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from monitor.alerts import AlertEngine
from monitor.alerts_settings import AlertSettings
from monitor.models import AGGREGATE_INTERFACE
from monitor.notifiers import build_notifiers
from monitor.retention import RetentionSettings
from monitor.service import SamplingService, WebSocketBridge
from monitor.storage import MetricsDatabase, Resolution, choose_resolution

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _resolve_app_config(
    app_config: Any | None,
    config_path: str | Path | None,
) -> Any | None:
    """Use an explicit AppConfig, else load via sibling config module if present."""
    if app_config is not None:
        return app_config
    try:
        from monitor.config import load_config
    except ImportError:
        return None
    return load_config(config_path)


class ConnectionManager:
    """Track active WebSocket clients and broadcast live samples."""

    def __init__(self) -> None:
        self.connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.connections:
            self.connections.remove(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for connection in self.connections:
            try:
                await connection.send_json(payload)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection)


def create_app(
    *,
    db_path: str = "monitor.db",
    interval: float = 1.0,
    history_size: int = 3600,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    retention_days: int = 7,
    retention: RetentionSettings | None = None,
    alert_settings: AlertSettings | None = None,
    app_config: Any | None = None,
    config_path: str | Path | None = None,
) -> FastAPI:
    retention_settings = retention or RetentionSettings(
        raw_retention_days=retention_days,
    ).with_env_overrides()
    database = MetricsDatabase(db_path)
    bridge = WebSocketBridge()
    manager = ConnectionManager()
    resolved_config = _resolve_app_config(app_config, config_path)
    settings = alert_settings or AlertSettings.resolve(app_config=resolved_config)
    alert_engine = AlertEngine(settings, interval=interval)
    notifiers = build_notifiers(settings.webhook_url)
    service = SamplingService(
        database,
        interval=interval,
        history_size=history_size,
        include=include or None,
        exclude=exclude or None,
        retention=retention_settings,
        on_sample=bridge.publish,
        alert_engine=alert_engine,
        notifiers=notifiers,
        error_delta_threshold=settings.error_delta_threshold,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        bridge.bind(loop, manager.broadcast)
        service.start()
        yield
        service.stop()
        database.close()

    app = FastAPI(title="Bandwidth Monitor", lifespan=lifespan)
    app.state.database = database
    app.state.service = service
    app.state.manager = manager
    app.state.alert_settings = settings

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def dashboard() -> FileResponse:
        index_path = STATIC_DIR / "index.html"
        if not index_path.exists():
            raise RuntimeError("Dashboard static files are missing.")
        return FileResponse(index_path)

    @app.get("/api/overview")
    async def overview(
        minutes: float = 5,
        resolution: Resolution = "auto",
    ) -> dict[str, Any]:
        return database.get_overview(minutes=minutes, resolution=resolution)

    @app.get("/api/history")
    async def history(
        interface: str = AGGREGATE_INTERFACE,
        minutes: float = 5,
        resolution: Resolution = "auto",
    ) -> dict[str, Any]:
        tier = choose_resolution(minutes, resolution)
        return {
            "interface": interface,
            "minutes": minutes,
            "resolution": tier,
            "samples": database.get_rate_history(
                interface,
                minutes=minutes,
                resolution=resolution,
            ),
        }

    @app.get("/api/interfaces")
    async def interfaces() -> dict[str, Any]:
        return {
            "snapshots": database.get_latest_interface_snapshots(),
            "rates": database.get_latest_interface_rates(),
        }

    @app.get("/api/health")
    async def health(limit: int = 50) -> dict[str, Any]:
        return {"events": database.get_health_events(limit=limit)}

    @app.get("/api/alerts")
    async def alerts(limit: int = 50) -> dict[str, Any]:
        return {"events": database.get_alert_events(limit=limit)}

    @app.get("/api/alerts/status")
    async def alerts_status() -> dict[str, Any]:
        return {
            "bandwidth_enabled": settings.bandwidth_enabled,
            "bandwidth_mbps_threshold": settings.bandwidth_mbps_threshold,
            "recv_bps_threshold": settings.recv_bps_threshold,
            "sent_bps_threshold": settings.sent_bps_threshold,
            "bandwidth_sustained_seconds": settings.bandwidth_sustained_seconds,
            "error_delta_threshold": settings.error_delta_threshold,
            "cooldown_seconds": settings.cooldown_seconds,
            "notifications_enabled": settings.notifications_enabled,
            "webhook_configured": bool(settings.webhook_url),
        }

    @app.websocket("/ws/live")
    async def live_updates(websocket: WebSocket) -> None:
        await manager.connect(websocket)
        try:
            latest = database.get_latest_rates()
            if latest is not None:
                await websocket.send_json({"type": "hello", "latest": latest})
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            manager.disconnect(websocket)

    return app
