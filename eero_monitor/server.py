"""FastAPI dashboard server with REST and WebSocket endpoints."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from eero_monitor.auth import load_credentials
from eero_monitor.client import EeroClient
from eero_monitor.models import AGGREGATE_DEVICE
from eero_monitor.service import SamplingService, WebSocketBridge
from eero_monitor.storage import MetricsDatabase

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
    db_path: str = "eero_monitor.db",
    interval: float = 5.0,
    history_size: int = 3600,
    retention_days: int = 7,
    client: Any | None = None,
) -> FastAPI:
    del history_size  # reserved for future in-memory buffer parity
    database = MetricsDatabase(db_path)
    bridge = WebSocketBridge()
    manager = ConnectionManager()
    if client is None:
        session, network_id = load_credentials()
        client = EeroClient(session, network_id)
    service = SamplingService(
        database,
        client=client,
        interval=interval,
        retention_days=retention_days,
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

    app = FastAPI(title="Eero Household Monitor", lifespan=lifespan)
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
    async def overview(minutes: float = 5) -> dict[str, Any]:
        return database.get_overview(minutes=minutes)

    @app.get("/api/history")
    async def history(
        device: str = AGGREGATE_DEVICE,
        minutes: float = 15,
    ) -> dict[str, Any]:
        return {
            "device": device,
            "minutes": minutes,
            "samples": database.get_rate_history(device, minutes=minutes),
        }

    @app.get("/api/devices")
    async def devices() -> dict[str, Any]:
        return {
            "snapshots": database.get_latest_device_snapshots(),
            "rates": database.get_latest_device_rates(),
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
