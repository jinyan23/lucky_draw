from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

import pandas as pd
from flask import Flask, jsonify, render_template, request

APP_DIR = Path(__file__).resolve().parent
PARTICIPANTS_XLSX = APP_DIR / "participant.xlsx"
PRIZES_XLSX = APP_DIR / "prize.xlsx"
OUTPUT_DIR = APP_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class LuckyDrawState:
    participants_remaining: list[dict[str, str]] = field(default_factory=list)
    prizes: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    drawn_participants_set: set[str] = field(default_factory=set)
    session_csv_path: Path | None = None
    load_error: str | None = None


app = Flask(__name__)
state = LuckyDrawState()
state_lock = Lock()


def error_response(message: str, status_code: int = 400):
    return jsonify({"ok": False, "message": message}), status_code


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(col).strip().lower() for col in df.columns]
    return df


def normalize_value(value: Any) -> str:
    if value is None:
        return ""
    if pd.isna(value):
        return ""
    return str(value).strip()


def parse_positive_int(value: Any, field_name: str) -> int:
    normalized = normalize_value(value)
    if not normalized:
        raise ValueError(f"{field_name} must be a positive integer")

    try:
        number = float(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc

    if not number.is_integer():
        raise ValueError(f"{field_name} must be a positive integer")

    parsed = int(number)
    if parsed < 1:
        raise ValueError(f"{field_name} must be at least 1")

    return parsed


def parse_participants(file_source: Any) -> list[dict[str, str]]:
    try:
        df = pd.read_excel(file_source)
    except Exception as exc:
        raise ValueError(f"Failed to read participants file: {exc}") from exc

    df = normalize_columns(df)
    required = {"participant", "group"}
    if not required.issubset(df.columns):
        raise ValueError("Participants file must contain columns: participant, group")

    parsed: list[dict[str, str]] = []
    seen_participants: set[str] = set()

    for _, row in df.iterrows():
        participant = normalize_value(row.get("participant"))
        group = normalize_value(row.get("group"))
        if not participant and not group:
            continue
        if not participant or not group:
            raise ValueError("Participants rows must have non-empty participant and group")
        if participant in seen_participants:
            raise ValueError(f"Duplicate participant found: {participant}")
        seen_participants.add(participant)
        parsed.append({"participant": participant, "group": group})

    if not parsed:
        raise ValueError("Participants file is empty after removing blank rows")

    return parsed


def parse_prizes(file_source: Any) -> list[dict[str, Any]]:
    try:
        df = pd.read_excel(file_source)
    except Exception as exc:
        raise ValueError(f"Failed to read prizes file: {exc}") from exc

    df = normalize_columns(df)
    required = {"prize_rank", "prize", "winner_num"}
    if not required.issubset(df.columns):
        raise ValueError("Prizes file must contain columns: prize_rank, prize, winner_num")

    parsed: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        prize_rank = normalize_value(row.get("prize_rank"))
        prize = normalize_value(row.get("prize"))
        winner_num_value = row.get("winner_num")
        winner_num_text = normalize_value(winner_num_value)

        if not prize_rank and not prize and not winner_num_text:
            continue
        if not prize_rank or not prize or not winner_num_text:
            raise ValueError("Prize rows must have non-empty prize_rank, prize, and winner_num")

        parsed.append(
            {
                "prize_id": str(len(parsed) + 1),
                "prize_rank": prize_rank,
                "prize": prize,
                "winner_num": parse_positive_int(winner_num_value, f"winner_num for {prize_rank} - {prize}"),
            }
        )

    if not parsed:
        raise ValueError("Prizes file is empty after removing blank rows")

    return parsed


def create_session_csv() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"winners_{timestamp}.csv"
    with path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=["prize_rank", "prize", "participant"])
        writer.writeheader()
    return path


def append_rows_to_csv(csv_path: Path, rows: list[dict[str, str]]) -> None:
    with csv_path.open("a", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=["prize_rank", "prize", "participant"])
        writer.writerows(rows)


def get_prize_by_id(prize_id: str) -> dict[str, Any] | None:
    for prize in state.prizes:
        if prize["prize_id"] == str(prize_id):
            return prize
    return None


def participant_exists_in_remaining(participant_name: str) -> bool:
    return any(item["participant"] == participant_name for item in state.participants_remaining)


def build_animation_pool(size: int) -> list[str]:
    names = [item["participant"] for item in state.participants_remaining]
    if not names:
        return []

    # Keep a meaningful list for client-side rotation.
    sample_size = max(size * 5, min(len(names), 12))
    sample_size = min(sample_size, len(names))
    return random.sample(names, sample_size)


def clear_state(error_message: str | None = None) -> None:
    state.participants_remaining = []
    state.prizes = []
    state.results = []
    state.drawn_participants_set = set()
    state.session_csv_path = None
    state.load_error = error_message


def load_backend_workbooks() -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    if not PARTICIPANTS_XLSX.exists():
        raise ValueError(f"Missing backend file: {PARTICIPANTS_XLSX.name}")
    if not PRIZES_XLSX.exists():
        raise ValueError(f"Missing backend file: {PRIZES_XLSX.name}")

    participants = parse_participants(PARTICIPANTS_XLSX)
    prizes = parse_prizes(PRIZES_XLSX)
    return participants, prizes


def initialize_state_from_backend() -> None:
    participants, prizes = load_backend_workbooks()
    state.participants_remaining = participants
    state.prizes = prizes
    state.results = []
    state.drawn_participants_set = set()
    state.session_csv_path = create_session_csv()
    state.load_error = None


def backend_not_ready_message() -> str:
    if state.load_error:
        return f"Could not load participant.xlsx and prize.xlsx: {state.load_error}"
    return "participant.xlsx and prize.xlsx are not ready on the backend"


@app.before_request
def ensure_backend_state_loaded():
    if state.session_csv_path is not None and state.prizes:
        return

    with state_lock:
        if state.session_csv_path is not None and state.prizes:
            return

        try:
            initialize_state_from_backend()
        except ValueError as exc:
            clear_state(str(exc))
        except Exception:
            clear_state("Unexpected error while reading backend workbooks")


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/state")
def get_state():
    is_ready = state.load_error is None and state.session_csv_path is not None and bool(state.prizes)
    return jsonify(
        {
            "ok": is_ready,
            "message": None if is_ready else backend_not_ready_message(),
            "prizes": state.prizes,
            "remaining_count": len(state.participants_remaining),
            "results": state.results,
            "csv_path": str(state.session_csv_path.relative_to(APP_DIR)) if state.session_csv_path else None,
        }
    )


@app.post("/api/participants/add")
def add_participant():
    payload = request.get_json(silent=True)
    if payload is None:
        return error_response("JSON body is required", 400)

    participant_name = normalize_value(payload.get("participant"))
    group = normalize_value(payload.get("group")) or "adhoc"

    if not participant_name:
        return error_response("participant is required", 400)

    with state_lock:
        if state.session_csv_path is None or not state.prizes:
            return error_response(backend_not_ready_message(), 400)

        if participant_name in state.drawn_participants_set:
            return error_response("Participant has already won and cannot be re-added", 409)

        if participant_exists_in_remaining(participant_name):
            return error_response("Participant already exists in current draw pool", 409)

        state.participants_remaining.append({"participant": participant_name, "group": group})

        return jsonify(
            {
                "ok": True,
                "message": "Participant added successfully",
                "remaining_count": len(state.participants_remaining),
            }
        )


@app.post("/api/draw")
def draw_winners():
    payload = request.get_json(silent=True)
    if payload is None:
        return error_response("JSON body is required", 400)

    prize_id = str(payload.get("prize_id", "")).strip()

    with state_lock:
        if state.session_csv_path is None or not state.prizes:
            return error_response(backend_not_ready_message(), 400)

        prize = get_prize_by_id(prize_id)
        if prize is None:
            return error_response("Invalid prize_id", 400)

        draw_count = int(prize["winner_num"])
        remaining_count = len(state.participants_remaining)
        if draw_count > remaining_count:
            return error_response(
                f"Not enough participants remaining. Prize requires {draw_count}, available {remaining_count}",
                409,
            )

        selected = random.sample(state.participants_remaining, draw_count)

        selected_names = {item["participant"] for item in selected}
        state.participants_remaining = [
            item for item in state.participants_remaining if item["participant"] not in selected_names
        ]

        final_winners: list[dict[str, Any]] = []
        csv_rows: list[dict[str, str]] = []

        for participant in selected:
            participant_name = participant["participant"]
            if participant_name in state.drawn_participants_set:
                return error_response(f"Duplicate draw detected for participant: {participant_name}", 500)

            state.drawn_participants_set.add(participant_name)
            result_row = {
                "prize_rank": prize["prize_rank"],
                "prize": prize["prize"],
                "participant": participant_name,
                "group": participant["group"],
                "redraw": False,
            }
            state.results.append(result_row)
            final_winners.append(
                {
                    "participant": participant_name,
                    "group": participant["group"],
                    "display_name": participant_name,
                    "redraw": False,
                }
            )
            csv_rows.append(
                {
                    "prize_rank": prize["prize_rank"],
                    "prize": prize["prize"],
                    "participant": participant_name,
                }
            )

        append_rows_to_csv(state.session_csv_path, csv_rows)
        animation_pool = build_animation_pool(draw_count)

        return jsonify(
            {
                "ok": True,
                "message": "Draw completed",
                "winner_num": draw_count,
                "animation_pool": animation_pool,
                "final_winners": final_winners,
                "remaining_count": len(state.participants_remaining),
            }
        )


@app.post("/api/redraw")
def redraw_winner():
    payload = request.get_json(silent=True)
    if payload is None:
        return error_response("JSON body is required", 400)

    prize_id = str(payload.get("prize_id", "")).strip()

    with state_lock:
        if state.session_csv_path is None or not state.prizes:
            return error_response(backend_not_ready_message(), 400)

        prize = get_prize_by_id(prize_id)
        if prize is None:
            return error_response("Invalid prize_id", 400)

        if not state.participants_remaining:
            return error_response("No participants remaining for redraw", 409)

        selected = random.choice(state.participants_remaining)
        state.participants_remaining = [
            item for item in state.participants_remaining if item["participant"] != selected["participant"]
        ]

        participant_name = selected["participant"]
        if participant_name in state.drawn_participants_set:
            return error_response(f"Duplicate redraw detected for participant: {participant_name}", 500)

        state.drawn_participants_set.add(participant_name)
        display_name = f"{participant_name} (R)"

        result_row = {
            "prize_rank": prize["prize_rank"],
            "prize": prize["prize"],
            "participant": participant_name,
            "group": selected["group"],
            "redraw": True,
        }
        state.results.append(result_row)

        append_rows_to_csv(
            state.session_csv_path,
            [
                {
                    "prize_rank": prize["prize_rank"],
                    "prize": prize["prize"],
                    "participant": display_name,
                }
            ],
        )

        return jsonify(
            {
                "ok": True,
                "message": "Redraw completed",
                "animation_pool": build_animation_pool(1),
                "final_winner": {
                    "participant": participant_name,
                    "group": selected["group"],
                    "display_name": display_name,
                    "redraw": True,
                },
                "remaining_count": len(state.participants_remaining),
            }
        )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
