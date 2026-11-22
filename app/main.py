import threading
import uuid
from dataclasses import dataclass, field
from typing import Optional
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.game_engine import ChessGame

app = FastAPI(title="Chaos Mutation Chess")
templates = Jinja2Templates(directory="templates")
server_lock = threading.Lock()


@dataclass
class MatchRoom:
    match_id: str
    title: str
    game: ChessGame = field(default_factory=ChessGame)
    players: dict[str, Optional[dict[str, str]]] = field(
        default_factory=lambda: {"white": None, "black": None}
    )
    spectators: dict[str, str] = field(default_factory=dict)  # user_id -> username

    def get_role(self, user_id: str) -> Optional[str]:
        for color in ("white", "black"):
            if self.players[color] and self.players[color]["id"] == user_id:
                return color
        if user_id in self.spectators:
            return "spectator"
        return None

    def summary(self) -> dict:
        return {
            "match_id": self.match_id,
            "title": self.title,
            "white": self.players["white"]["name"] if self.players["white"] else None,
            "black": self.players["black"]["name"] if self.players["black"] else None,
            "spectator_count": len(self.spectators),
            "turn": self.game.turn,
            "game_over": self.game.game_over,
            "winner": self.game.winner,
            "move_count": max(0, len(self.game.history) - 1),
        }

    def full_state(self) -> dict:
        state = self.game.to_dict()
        state["match_id"] = self.match_id
        state["title"] = self.title
        state["players"] = {
            "white": self.players["white"]["name"] if self.players["white"] else None,
            "black": self.players["black"]["name"] if self.players["black"] else None,
        }
        state["spectators"] = list(self.spectators.values())
        return state


# Active users: user_id -> username
users: dict[str, str] = {}
# Active matches: match_id -> MatchRoom
matches: dict[str, MatchRoom] = {}


class RegisterPayload(BaseModel):
    username: str


class CreateMatchPayload(BaseModel):
    user_id: str
    title: Optional[str] = ""
    preferred_color: Optional[str] = "white"  # "white" | "black"


class JoinMatchPayload(BaseModel):
    user_id: str
    match_id: str
    mode: str = "auto"  # "auto" | "white" | "black" | "spectate"


class SquareActionPayload(BaseModel):
    user_id: str
    match_id: str
    row: int
    col: int


class MatchUserPayload(BaseModel):
    user_id: str
    match_id: str


def lobby_snapshot() -> list[dict]:
    return [room.summary() for room in matches.values()]


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.add(websocket)
        with server_lock:
            await websocket.send_json({"type": "lobby", "matches": lobby_snapshot()})

    def disconnect(self, websocket: WebSocket) -> None:
        self.connections.discard(websocket)

    async def broadcast_lobby(self) -> None:
        with server_lock:
            payload = {"type": "lobby", "matches": lobby_snapshot()}
        await self._send_all(payload)

    async def broadcast_match(self, match_id: str) -> None:
        with server_lock:
            room = matches.get(match_id)
            if not room:
                return
            match_payload = {"type": "match_state", "match_id": match_id, "state": room.full_state()}
            lobby_payload = {"type": "lobby", "matches": lobby_snapshot()}
        await self._send_all(match_payload)
        await self._send_all(lobby_payload)

    async def _send_all(self, payload: dict) -> None:
        stale: list[WebSocket] = []
        for ws in list(self.connections):
            try:
                await ws.send_json(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


manager = ConnectionManager()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/register")
async def register_user(payload: RegisterPayload):
    name = payload.username.strip()
    if not name:
        return {"ok": False, "error": "Please enter a username."}

    with server_lock:
        for uid, existing_name in users.items():
            if existing_name.lower() == name.lower():
                return {
                    "ok": True,
                    "user_id": uid,
                    "username": existing_name,
                    "matches": lobby_snapshot(),
                }
        user_id = str(uuid.uuid4())
        users[user_id] = name
        snap = lobby_snapshot()

    return {"ok": True, "user_id": user_id, "username": name, "matches": snap}


@app.get("/api/matches")
def list_matches():
    with server_lock:
        return {"ok": True, "matches": lobby_snapshot()}


@app.post("/api/matches/create")
async def create_match(payload: CreateMatchPayload):
    with server_lock:
        username = users.get(payload.user_id)
        if not username:
            return {"ok": False, "error": "Register a username first."}

        match_id = str(uuid.uuid4())[:8]
        title = (payload.title or "").strip() or f"{username}'s Arena #{len(matches) + 1}"
        room = MatchRoom(match_id=match_id, title=title)

        color = "black" if payload.preferred_color == "black" else "white"
        room.players[color] = {"id": payload.user_id, "name": username}
        room.game.history.insert(0, f"{username} created match as {color.upper()}.")
        matches[match_id] = room
        state = room.full_state()

    await manager.broadcast_lobby()
    return {"ok": True, "match_id": match_id, "role": color, "state": state}


@app.post("/api/matches/join")
async def join_match(payload: JoinMatchPayload):
    with server_lock:
        username = users.get(payload.user_id)
        if not username:
            return {"ok": False, "error": "Register a username first."}

        room = matches.get(payload.match_id)
        if not room:
            return {"ok": False, "error": "Match not found."}

        # Already seated or spectating in this room?
        existing_role = room.get_role(payload.user_id)
        if existing_role:
            return {"ok": True, "match_id": room.match_id, "role": existing_role, "state": room.full_state()}

        mode = payload.mode.lower()
        assigned_role: Optional[str] = None

        if mode == "spectate":
            assigned_role = "spectator"
        elif mode in ("white", "black"):
            if room.players[mode] is not None:
                return {"ok": False, "error": f"{mode.capitalize()} seat is already taken."}
            assigned_role = mode
        else:  # "auto"
            if room.players["white"] is None:
                assigned_role = "white"
            elif room.players["black"] is None:
                assigned_role = "black"
            else:
                assigned_role = "spectator"

        if assigned_role in ("white", "black"):
            room.players[assigned_role] = {"id": payload.user_id, "name": username}
            room.game.history.insert(0, f"{username} joined as {assigned_role.upper()}.")
        else:
            room.spectators[payload.user_id] = username
            room.game.history.insert(0, f"{username} is spectating.")

        state = room.full_state()

    await manager.broadcast_match(room.match_id)
    return {"ok": True, "match_id": room.match_id, "role": assigned_role, "state": state}


@app.post("/api/select")
async def select_piece(payload: SquareActionPayload):
    with server_lock:
        room = matches.get(payload.match_id)
        if not room:
            return {"ok": False, "error": "Match not found."}
        role = room.get_role(payload.user_id)
        if role not in ("white", "black"):
            return {"ok": False, "error": "Spectators are in view-only mode.", "state": room.full_state()}
        if room.game.turn != role:
            return {"ok": False, "error": f"It is {room.game.turn.upper()}'s turn.", "state": room.full_state()}

        result = room.game.select_piece(payload.row, payload.col)
        result["state"] = room.full_state()

    if result.get("ok"):
        await manager.broadcast_match(room.match_id)
    return result


@app.post("/api/move")
async def move_piece(payload: SquareActionPayload):
    with server_lock:
        room = matches.get(payload.match_id)
        if not room:
            return {"ok": False, "error": "Match not found."}
        role = room.get_role(payload.user_id)
        if role not in ("white", "black"):
            return {"ok": False, "error": "Spectators are in view-only mode.", "state": room.full_state()}
        if room.game.turn != role:
            return {"ok": False, "error": f"It is {room.game.turn.upper()}'s turn.", "state": room.full_state()}

        result = room.game.move_selected(payload.row, payload.col)
        result["state"] = room.full_state()

    if result.get("ok"):
        await manager.broadcast_match(room.match_id)
    return result


@app.post("/api/forfeit")
async def forfeit_turn(payload: MatchUserPayload):
    with server_lock:
        room = matches.get(payload.match_id)
        if not room:
            return {"ok": False, "error": "Match not found."}
        role = room.get_role(payload.user_id)
        if role not in ("white", "black"):
            return {"ok": False, "error": "Spectators cannot forfeit.", "state": room.full_state()}
        if room.game.turn != role:
            return {"ok": False, "error": f"Only {role.upper()} can forfeit on their turn.", "state": room.full_state()}

        result = room.game.forfeit_turn()
        result["state"] = room.full_state()

    if result.get("ok"):
        await manager.broadcast_match(room.match_id)
    return result


@app.post("/api/reset")
async def reset_game(payload: MatchUserPayload):
    with server_lock:
        room = matches.get(payload.match_id)
        if not room:
            return {"ok": False, "error": "Match not found."}
        role = room.get_role(payload.user_id)
        if role not in ("white", "black"):
            return {"ok": False, "error": "Only seated players can reset this match.", "state": room.full_state()}

        room.game.reset()
        w_name = room.players["white"]["name"] if room.players["white"] else "Open"
        b_name = room.players["black"]["name"] if room.players["black"] else "Open"
        room.game.history.insert(0, f"Board reset. White: {w_name} | Black: {b_name}")
        state = room.full_state()

    await manager.broadcast_match(room.match_id)
    return {"ok": True, "state": state}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)