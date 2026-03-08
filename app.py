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
OUTPUT_DIR = APP_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class LuckyDrawState:
    participants_remaining: list[dict[str, str]] = field(default_factory=list)
    prizes: list[dict[str, str]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    drawn_participants_set: set[str] = field(default_factory=set)
    session_csv_path: Path | None = None


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


def validate_xlsx_filename(filename: str | None) -> bool:
    return bool(filename and filename.lower().endswith(".xlsx"))


def parse_participants(file_storage) -> list[dict[str, str]]:
    try:
        df = pd.read_excel(file_storage)
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


def parse_prizes(file_storage) -> list[dict[str, str]]:
    try:
        df = pd.read_excel(file_storage)
    except Exception as exc:
        raise ValueError(f"Failed to read prizes file: {exc}") from exc

    df = normalize_columns(df)
    required = {"prize_rank", "prize"}
    if not required.issubset(df.columns):
        raise ValueError("Prizes file must contain columns: prize_rank, prize")

    parsed: list[dict[str, str]] = []
    for idx, row in df.iterrows():
        prize_rank = normalize_value(row.get("prize_rank"))
        prize = normalize_value(row.get("prize"))
        if not prize_rank and not prize:
            continue
        if not prize_rank or not prize:
            raise ValueError("Prize rows must have non-empty prize_rank and prize")
        parsed.append(
            {
                "prize_id": str(len(parsed) + 1),
                "prize_rank": prize_rank,
                "prize": prize,
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


def get_prize_by_id(prize_id: str) -> dict[str, str] | None:
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


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/state")
def get_state():
    return jsonify(
        {
            "ok": True,
            "prizes": state.prizes,
            "remaining_count": len(state.participants_remaining),
            "results": state.results,
            "csv_path": str(state.session_csv_path.relative_to(APP_DIR)) if state.session_csv_path else None,
        }
    )


@app.post("/api/upload")
def upload_files():
    participants_file = request.files.get("participants_file")
    prizes_file = request.files.get("prizes_file")

    if participants_file is None or prizes_file is None:
        return error_response("Both participants_file and prizes_file are required", 400)

    if not validate_xlsx_filename(participants_file.filename) or not validate_xlsx_filename(prizes_file.filename):
        return error_response("Both files must be .xlsx", 400)

    try:
        participants = parse_participants(participants_file)
        prizes = parse_prizes(prizes_file)
    except ValueError as exc:
        return error_response(str(exc), 400)
    except Exception:
        return error_response("Unexpected error while reading uploaded files", 500)

    with state_lock:
        state.participants_remaining = participants
        state.prizes = prizes
        state.results = []
        state.drawn_participants_set = set()
        state.session_csv_path = create_session_csv()

    return jsonify(
        {
            "ok": True,
            "message": "Files uploaded successfully",
            "prizes": state.prizes,
            "remaining_count": len(state.participants_remaining),
            "csv_path": str(state.session_csv_path.relative_to(APP_DIR)),
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
            return error_response("Please upload participants and prizes first", 400)

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
    draw_count_raw = payload.get("draw_count")

    try:
        draw_count = int(draw_count_raw)
    except (TypeError, ValueError):
        return error_response("draw_count must be an integer", 400)

    with state_lock:
        if state.session_csv_path is None or not state.prizes:
            return error_response("Please upload participants and prizes first", 400)

        prize = get_prize_by_id(prize_id)
        if prize is None:
            return error_response("Invalid prize_id", 400)

        if draw_count < 1:
            return error_response("draw_count must be at least 1", 400)

        remaining_count = len(state.participants_remaining)
        if draw_count > remaining_count:
            return error_response(
                f"Not enough participants remaining. Requested {draw_count}, available {remaining_count}",
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
            return error_response("Please upload participants and prizes first", 400)

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
