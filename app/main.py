import asyncio
import threading
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.game_engine import ChessGame

app = FastAPI(title="Chaos Mutation Chess")
templates = Jinja2Templates(directory="templates")
game = ChessGame()
game_lock = threading.Lock()


class SquarePayload(BaseModel):
    row: int
    col: int


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)
        # Send current state immediately upon connect
        with game_lock:
            await websocket.send_json({"type": "state", "state": game.to_dict()})

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.discard(websocket)

    async def broadcast_state(self) -> None:
        with game_lock:
            payload = {"type": "state", "state": game.to_dict()}
        stale: list[WebSocket] = []
        for ws in list(self.active_connections):
            try:
                await ws.send_json(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


manager = ConnectionManager()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "state": game.to_dict()})


@app.get("/api/state")
def get_state():
    with game_lock:
        return game.to_dict()


@app.post("/api/select")
async def select_piece(payload: SquarePayload):
    with game_lock:
        result = game.select_piece(payload.row, payload.col)
    if result.get("ok"):
        await manager.broadcast_state()
    return result


@app.post("/api/move")
async def move_piece(payload: SquarePayload):
    with game_lock:
        result = game.move_selected(payload.row, payload.col)
    if result.get("ok"):
        await manager.broadcast_state()
    return result


@app.post("/api/forfeit")
async def forfeit_turn():
    with game_lock:
        result = game.forfeit_turn()
    if result.get("ok"):
        await manager.broadcast_state()
    return result


@app.post("/api/reset")
async def reset_game():
    with game_lock:
        game.reset()
    await manager.broadcast_state()
    return {"ok": True, "state": game.to_dict()}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection open; handle optional ping/refresh messages
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
            elif data == "sync":
                with game_lock:
                    await websocket.send_json({"type": "state", "state": game.to_dict()})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)