"""FastAPI dashboard server with REST and WebSocket endpoints."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from monitor.models import AGGREGATE_INTERFACE
from monitor.retention import RetentionSettings
from monitor.service import SamplingService, WebSocketBridge
from monitor.storage import MetricsDatabase, Resolution, choose_resolution

STATIC_DIR = Path(__file__).resolve().parent / "static"


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
) -> FastAPI:
    retention_settings = retention or RetentionSettings(
        raw_retention_days=retention_days,
    ).with_env_overrides()
    database = MetricsDatabase(db_path)
    bridge = WebSocketBridge()
    manager = ConnectionManager()
    service = SamplingService(
        database,
        interval=interval,
        history_size=history_size,
        include=include or None,
        exclude=exclude or None,
        retention=retention_settings,
        on_sample=bridge.publish,
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
