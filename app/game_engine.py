from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

PIECE_TYPES = ["pawn", "rook", "knight", "bishop", "queen", "king"]
MOVEMENT_TEMPLATES = ["rook", "knight", "bishop", "queen", "king"]


@dataclass
class MutationFlag:
    active: bool = False
    color: Optional[str] = None
    target_piece: Optional[str] = None
    mimics_piece: Optional[str] = None
    message: str = "No mutation active yet — capture a piece to trigger a mutation."


@dataclass
class ChessGame:
    board: list[list[Optional[dict]]] = field(default_factory=list)
    turn: str = "white"  # "white" or "black"
    selected_square: Optional[tuple[int, int]] = None
    # Per-color movement overrides: { "white": { "bishop": "queen" }, "black": {} }
    mutations: dict[str, dict[str, str]] = field(
        default_factory=lambda: {"white": {}, "black": {}}
    )
    flag: MutationFlag = field(default_factory=MutationFlag)
    history: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.board:
            self.reset()

    def reset(self) -> None:
        back_rank = ["rook", "knight", "bishop", "queen", "king", "bishop", "knight", "rook"]
        self.board = [[None for _ in range(8)] for _ in range(8)]

        for col, ptype in enumerate(back_rank):
            self.board[0][col] = {"color": "black", "type": ptype}
            self.board[1][col] = {"color": "black", "type": "pawn"}
            self.board[6][col] = {"color": "white", "type": "pawn"}
            self.board[7][col] = {"color": "white", "type": "rook" if ptype == "rook" else ptype}

        self.turn = "white"
        self.selected_square = None
        self.mutations = {"white": {}, "black": {}}
        self.flag = MutationFlag()
        self.history = ["New game started. White to move."]

    def _in_bounds(self, r: int, c: int) -> bool:
        return 0 <= r < 8 and 0 <= c < 8

    def select_piece(self, r: int, c: int) -> dict:
        if not self._in_bounds(r, c):
            return {"ok": False, "error": "Square out of bounds."}

        # If a piece is already selected, player is locked to that piece unless they forfeit
        if self.selected_square is not None:
            if self.selected_square == (r, c):
                return {
                    "ok": True,
                    "selected": self.selected_square,
                    "valid_moves": self.get_valid_moves(r, c),
                    "state": self.to_dict(),
                }
            return {
                "ok": False,
                "error": "You already selected a piece! You must move it or click Forfeit Turn.",
                "state": self.to_dict(),
            }

        piece = self.board[r][c]
        if not piece or piece["color"] != self.turn:
            return {
                "ok": False,
                "error": f"Select one of {self.turn}'s pieces.",
                "state": self.to_dict(),
            }

        moves = self.get_valid_moves(r, c)
        if not moves:
            return {
                "ok": False,
                "error": "That piece has no legal moves right now. Pick another or Forfeit Turn.",
                "state": self.to_dict(),
            }

        self.selected_square = (r, c)
        return {
            "ok": True,
            "selected": self.selected_square,
            "valid_moves": moves,
            "state": self.to_dict(),
        }

    def forfeit_turn(self) -> dict:
        previous_turn = self.turn
        had_selection = self.selected_square is not None
        self.selected_square = None
        self.turn = "black" if self.turn == "white" else "white"
        note = (
            f"{previous_turn.capitalize()} forfeited their turn after selecting a piece."
            if had_selection
            else f"{previous_turn.capitalize()} forfeited their turn."
        )
        self.history.insert(0, note)
        return {"ok": True, "message": note, "state": self.to_dict()}

    def _ray_moves(self, r: int, c: int, color: str, directions: list[tuple[int, int]]) -> list[tuple[int, int]]:
        moves: list[tuple[int, int]] = []
        for dr, dc in directions:
            nr, nc = r + dr, c + dc
            while self._in_bounds(nr, nc):
                target = self.board[nr][nc]
                if target is None:
                    moves.append((nr, nc))
                else:
                    if target["color"] != color:
                        moves.append((nr, nc))
                    break
                nr += dr
                nc += dc
        return moves

    def _step_moves(self, r: int, c: int, color: str, offsets: list[tuple[int, int]]) -> list[tuple[int, int]]:
        moves: list[tuple[int, int]] = []
        for dr, dc in offsets:
            nr, nc = r + dr, c + dc
            if self._in_bounds(nr, nc):
                target = self.board[nr][nc]
                if target is None or target["color"] != color:
                    moves.append((nr, nc))
        return moves

    def _standard_pawn_moves(self, r: int, c: int, color: str) -> list[tuple[int, int]]:
        moves: list[tuple[int, int]] = []
        direction = -1 if color == "white" else 1
        start_row = 6 if color == "white" else 1

        nr = r + direction
        if self._in_bounds(nr, c) and self.board[nr][c] is None:
            moves.append((nr, c))
            nr2 = r + 2 * direction
            if r == start_row and self._in_bounds(nr2, c) and self.board[nr2][c] is None:
                moves.append((nr2, c))

        for dc in (-1, 1):
            nc = c + dc
            if self._in_bounds(nr, nc):
                target = self.board[nr][nc]
                if target is not None and target["color"] != color:
                    moves.append((nr, nc))

        return moves

    def get_valid_moves(self, r: int, c: int) -> list[tuple[int, int]]:
        piece = self.board[r][c]
        if not piece:
            return []

        color = piece["color"]
        base_type = piece["type"]
        effective_pattern = self.mutations.get(color, {}).get(base_type, base_type)

        if effective_pattern == "pawn":
            return self._standard_pawn_moves(r, c, color)
        if effective_pattern == "rook":
            return self._ray_moves(r, c, color, [(-1, 0), (1, 0), (0, -1), (0, 1)])
        if effective_pattern == "bishop":
            return self._ray_moves(r, c, color, [(-1, -1), (-1, 1), (1, -1), (1, 1)])
        if effective_pattern == "queen":
            return self._ray_moves(
                r,
                c,
                color,
                [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)],
            )
        if effective_pattern == "knight":
            return self._step_moves(
                r,
                c,
                color,
                [(-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)],
            )
        if effective_pattern == "king":
            return self._step_moves(
                r,
                c,
                color,
                [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)],
            )
        return []

    def move_selected(self, to_r: int, to_c: int) -> dict:
        if self.selected_square is None:
            return {"ok": False, "error": "Select a piece first."}

        from_r, from_c = self.selected_square
        valid = self.get_valid_moves(from_r, from_c)
        if (to_r, to_c) not in valid:
            return {
                "ok": False,
                "error": "Illegal move for the selected piece! Move it to a highlighted square or Forfeit Turn.",
                "state": self.to_dict(),
            }

        moving_piece = self.board[from_r][from_c]
        captured_piece = self.board[to_r][to_c]

        # Execute move
        self.board[to_r][to_c] = moving_piece
        self.board[from_r][from_c] = None

        # Pawn promotion to queen on last rank
        promoted = False
        if moving_piece["type"] == "pawn" and (to_r == 0 or to_r == 7):
            moving_piece["type"] = "queen"
            promoted = True

        move_desc = (
            f"{moving_piece['color'].capitalize()} {moving_piece['type']} "
            f"{self._coord(from_r, from_c)}->{self._coord(to_r, to_c)}"
        )
        if captured_piece:
            move_desc += f" (captured {captured_piece['type']})"
        if promoted:
            move_desc += " [promoted to queen]"

        # Trigger mutation on capture
        if captured_piece is not None:
            target_piece = random.choice(PIECE_TYPES)
            current_pattern = self.mutations[moving_piece["color"]].get(target_piece, target_piece)
            candidates = [m for m in MOVEMENT_TEMPLATES if m != current_pattern]
            mimics_piece = random.choice(candidates)

            self.mutations[moving_piece["color"]][target_piece] = mimics_piece
            self.flag = MutationFlag(
                active=True,
                color=moving_piece["color"],
                target_piece=target_piece,
                mimics_piece=mimics_piece,
                message=(
                    f"MUTATION FLAG: {moving_piece['color'].capitalize()}'s {target_piece.upper()} "
                    f"can now move like a {mimics_piece.upper()}! "
                    f"(Triggered when {moving_piece['type']} captured {captured_piece['type']})"
                ),
            )
            move_desc += f" | {target_piece} now moves like {mimics_piece}"

        self.history.insert(0, move_desc)
        self.selected_square = None
        self.turn = "black" if self.turn == "white" else "white"

        return {"ok": True, "state": self.to_dict()}

    @staticmethod
    def _coord(r: int, c: int) -> str:
        return f"{'abcdefgh'[c]}{8 - r}"

    def to_dict(self) -> dict:
        valid_moves = (
            self.get_valid_moves(*self.selected_square)
            if self.selected_square is not None
            else []
        )
        return {
            "board": self.board,
            "turn": self.turn,
            "selected_square": list(self.selected_square) if self.selected_square else None,
            "valid_moves": [list(m) for m in valid_moves],
            "mutations": self.mutations,
            "flag": {
                "active": self.flag.active,
                "color": self.flag.color,
                "target_piece": self.flag.target_piece,
                "mimics_piece": self.flag.mimics_piece,
                "message": self.flag.message,
            },
            "history": self.history[:20],
        }