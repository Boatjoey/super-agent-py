"""The persisted timestamp and identifier format.

Timestamps are RFC 3339 with nine fractional digits and trailing zeros trimmed,
and session and turn identifiers are formatted from ``time.time_ns``, because
``strftime("%f")`` only reaches microseconds. The checked-in session store holds
stamps at that precision, so reading one of its records and writing it back must
return the digits it holds.
"""

from __future__ import annotations

import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from super_agent import store, timeutil

SESSION_STORE_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "session_store"
FIXTURE_SESSION_ID = "20260916T120000123456789"
#: ``2026-09-16T12:00:00.123456789Z``, the instant the fixture metadata carries.
FIXTURE_INSTANT_NS = 1_789_560_000_123_456_789


def fixture_store(tmp_path: Path) -> store.Store:
    """The checked-in session store, copied so a test may rewrite it."""
    sessions = tmp_path / "sessions"
    shutil.copytree(SESSION_STORE_FIXTURE, sessions)
    return store.new(str(sessions))


def session_meta(tmp_path: Path) -> dict[str, object]:
    path = tmp_path / "sessions" / FIXTURE_SESSION_ID / "meta.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_metadata_rewrite_preserves_nanosecond_timestamps(tmp_path: Path) -> None:
    st = fixture_store(tmp_path)

    st.rename_session(store.SessionID(FIXTURE_SESSION_ID), "retitled")

    meta = session_meta(tmp_path)
    assert meta["title"] == "retitled"
    assert meta["created_at"] == "2026-09-16T12:00:00.123456789Z"
    assert meta["current_turn_id"] == "20260916T120000.123456789"


def test_log_rewrite_preserves_nanosecond_timestamps(tmp_path: Path) -> None:
    st = fixture_store(tmp_path)

    st.truncate_after(store.SessionID(FIXTURE_SESSION_ID), 4)

    events_path = tmp_path / "sessions" / FIXTURE_SESSION_ID / "events.jsonl"
    times = [json.loads(line)["time"] for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert len(times) == 5
    assert times[2] == "2026-09-16T12:00:00.123456789Z"


def test_parse_and_format_round_trip_stored_stamps() -> None:
    for text in (
        "2026-09-16T12:00:00.123456789Z",
        "2026-09-16T09:44:54.164417Z",
        "2026-09-16T12:00:00.5Z",
        "2026-09-16T12:00:01Z",
        "2026-09-16T14:00:00.123456789+02:00",
    ):
        assert timeutil.format_rfc3339_nano(timeutil.parse_rfc3339_nano(text)) == text


def test_format_trims_trailing_zeros_of_a_microsecond_instant() -> None:
    assert (
        timeutil.format_rfc3339_nano(datetime(2026, 9, 16, 12, 0, 0, 123456, tzinfo=UTC))
        == "2026-09-16T12:00:00.123456Z"
    )
    assert timeutil.format_rfc3339_nano(datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)) == "2026-09-16T12:00:00Z"


def test_now_is_formatted_from_the_nanosecond_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time_ns", lambda: FIXTURE_INSTANT_NS)

    assert timeutil.now_rfc3339_nano() == "2026-09-16T12:00:00.123456789Z"


def test_generated_identifiers_carry_nanosecond_precision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time_ns", lambda: FIXTURE_INSTANT_NS)

    assert timeutil.now_id() == FIXTURE_SESSION_ID
    assert timeutil.now_turn_id() == "20260916T120000.123456789"


def test_a_generated_identifier_names_an_instant_between_two_clock_reads() -> None:
    before = time.time_ns()
    identifier = timeutil.now_id()
    after = time.time_ns()

    stamp = datetime.strptime(identifier[:15], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    instant = int(stamp.timestamp()) * 1_000_000_000 + int(identifier[15:])
    assert before <= instant <= after
