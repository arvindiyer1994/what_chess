from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.game_engine import ChessGame

app = FastAPI(title="Chaos Mutation Chess")
templates = Jinja2Templates(directory="templates")
game = ChessGame()


class SquarePayload(BaseModel):
    row: int
    col: int


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "state": game.to_dict()})


@app.get("/api/state")
def get_state():
    return game.to_dict()


@app.post("/api/select")
def select_piece(payload: SquarePayload):
    return game.select_piece(payload.row, payload.col)


@app.post("/api/move")
def move_piece(payload: SquarePayload):
    return game.move_selected(payload.row, payload.col)


@app.post("/api/forfeit")
def forfeit_turn():
    return game.forfeit_turn()


@app.post("/api/reset")
def reset_game():
    game.reset()
    return {"ok": True, "state": game.to_dict()}