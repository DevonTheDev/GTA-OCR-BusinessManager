"""Opt-in generated-image diagnostics, not GTA screenshots or Windows OCR.

Production crops and preprocessing feed the installed Tesseract diagnostic
adapter, then the real capture loop, trackers, balance parser and SQLite.
"""

import os

import pytest

if os.environ.get("GTA_RUN_OCR_TESTS") != "1":
    pytest.skip("Set GTA_RUN_OCR_TESTS=1 for synthetic-image OCR integration", allow_module_level=True)

from src.game.state_machine import GameState
from tests.test_app_accounting import app as app
from tests.test_inline_mission_results import LABELS
from tests.test_mission_ocr_images import (
    FONT_PATH, Image, ImageDraw, ImageFont, REGIONS, RESOLUTIONS, np, run_frames, synthetic_frame,
)
from tests.test_terminal_mission_identity_ownership import mission_stats, stored


def money_frame(resolution, balance=1000, **texts):
    """Render a real balance image alongside the mission crops, with no OCR stubs."""
    frame = synthetic_frame(resolution, **texts)
    image = Image.fromarray(frame[:, :, ::-1])
    draw = ImageDraw.Draw(image)
    scale = resolution[1] / 1080
    font = ImageFont.truetype(str(FONT_PATH), round(24 * scale))
    left, top, right, bottom = REGIONS.money_display.to_absolute(*resolution)
    position = (left + round(12 * scale), top + round(3 * scale))
    text = f"${balance:,}"
    bounds = draw.textbbox(position, text, font=font)
    assert left <= bounds[0] < bounds[2] <= right
    assert top <= bounds[1] < bounds[3] <= bottom
    draw.text(position, text, font=font, fill="white")
    return np.asarray(image)[:, :, ::-1].copy()


def assert_transcription(checkpoints, expected):
    actual = [(result.mission_text, result.objective_text, result.banner_text) for result, _, _ in checkpoints]
    print({"expected_crops": expected, "actual_crops": actual})
    assert actual == expected, "Generated fixture did not reproduce the required OCR text"


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=["720p", "1080p", "1440p"])
@pytest.mark.parametrize("label,outcome", LABELS)
@pytest.mark.parametrize("title_first", [False, True])
def test_rendered_inline_result_cannot_invent_activity(app, monkeypatch, resolution, label, outcome, title_first):
    text = f"Headhunter {label}" if title_first else f"{label} Headhunter"
    checkpoints, _ = run_frames(app, monkeypatch, [
        money_frame(resolution, banner_text=text),
        money_frame(resolution, balance=1250, banner_text="Headhunter"),
        money_frame(resolution, balance=1250, banner_text=label + "\nHeadhunter"),
    ])
    assert_transcription(checkpoints, [("", "", text), ("", "", "Headhunter"), ("", "", label + "\nHeadhunter")])
    first, held, repeated = [item[0] for item in checkpoints]
    assert first.game_state == (GameState.MISSION_COMPLETE if outcome == "complete" else GameState.MISSION_FAILED)
    assert first.mission.outcome == outcome
    assert first.activity_name == held.activity_name == repeated.activity_name == ""
    assert held.game_state == GameState.UNKNOWN and held.state_confidence == 0.0
    assert all(rows == [] for _, rows, _ in checkpoints)
    assert app._activity_tracker.current_activity is None
    assert app._activity_tracker.completed_activities == []
    assert mission_stats(app) == (0, 0, 0, 0)
    assert app.cooldown_tracker.get_active_cooldowns() == []
    assert [result.money.display_value for result, _, _ in checkpoints] == [1000, 1250, 1250]
    assert held.money_change == 250 and app.current_money == 1250
    assert app.session_earnings == 250
    assert [(row["amount"], row["source"]) for row in stored(app)["earnings"]] == [
        (250, "Mission" if outcome == "complete" else ""),
    ]


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=["720p", "1080p", "1440p"])
@pytest.mark.parametrize("label,outcome", LABELS[:2])
def test_rendered_inline_completion_preserves_next_mission_and_money_ownership(app, monkeypatch, resolution, label, outcome):
    completed = []
    app.on_mission_complete(completed.append)
    banners = ["Headhunter", label + " Headhunter", "Headhunter", "Sightseer",
               "Headhunter " + label, "Sightseer MISSION PASSED", "MISSION PASSED Sightseer"]
    balances = [1000, 1250, 1250, 1250, 1500, 1750, 1750]
    checkpoints, _ = run_frames(app, monkeypatch, [
        money_frame(resolution, balance=balance, banner_text=banner)
        for banner, balance in zip(banners, balances)
    ])
    assert_transcription(checkpoints, [("", "", banner) for banner in banners])
    assert [result.money.display_value for result, _, _ in checkpoints] == balances
    assert checkpoints[2][0].game_state == GameState.UNKNOWN
    assert not checkpoints[2][0].activity_name
    assert checkpoints[3][0].activity_name == "Sightseer"
    rejected = checkpoints[4][0]
    assert rejected.game_state == GameState.UNKNOWN and rejected.state_confidence == 0.0
    assert "conflict" in rejected.state_reason.lower()
    assert rejected.activity_name == "Sightseer" and rejected.money_change == 250
    assert [len(rows) for _, rows, _ in checkpoints] == [0, 1, 1, 1, 1, 2, 2]
    assert checkpoints[-2][1] == checkpoints[-1][1]
    success = outcome == "complete"
    assert [(row["name"], row["success"], row["earnings"]) for row in checkpoints[-1][1]] == [
        ("Headhunter", success, 250 if success else 0), ("Sightseer", True, 500),
    ]
    assert mission_stats(app) == (2, 1 + int(success), int(not success), 0)
    assert len(completed) == 1 + int(success)
    assert (app.cooldown_tracker.get_cooldown("headhunter") is not None) is success
    assert app.cooldown_tracker.get_cooldown("sightseer") is not None
    assert app._activity_tracker.current_activity is None
    assert app.session_earnings == 750 and app.current_money == 1750
    assert [(row["amount"], row["source"]) for row in stored(app)["earnings"]] == [
        (250, "Headhunter" if success else ""), (250, ""), (250, "Sightseer"),
    ]


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=["720p", "1080p", "1440p"])
@pytest.mark.parametrize("mission,banner,outcome", [
    ("", "MISSION PASSED\nHeadhunter", "complete"),
    ("", "MISSION FAILED\nHeadhunter", "failed"),
    ("", "MISSION PASSED Mystery Job", None),
    ("", "Headhunter MISSION PASSED if you win", None),
    ("MISSION", "PASSED Headhunter", None),
    ("MISSION PASSED Hostile", "Takeover", None),
    ("", "MISSION PASSED Headhunter MISSION FAILED", "conflicting"),
], ids=["newline-pass", "newline-fail", "unknown", "conditional", "split-result", "split-title", "contradictory"])
def test_rendered_unchanged_result_boundaries(app, monkeypatch, resolution, mission, banner, outcome):
    checkpoints, _ = run_frames(app, monkeypatch, [
        money_frame(resolution, banner_text="Headhunter"),
        money_frame(resolution, balance=1250, mission_text=mission, banner_text=banner),
    ])
    assert_transcription(checkpoints, [("", "", "Headhunter"), (mission, "", banner)])
    observed = checkpoints[-1][0]
    assert observed.mission.outcome == outcome
    assert observed.money_change == 250 and app.current_money == 1250
    if outcome in (None, "conflicting"):
        assert observed.game_state not in (GameState.MISSION_COMPLETE, GameState.MISSION_FAILED)
        assert app._activity_tracker.current_activity.name == "Headhunter"
        assert checkpoints[-1][1] == []
        assert mission_stats(app) == (0, 0, 0, 0)
        assert app.cooldown_tracker.get_active_cooldowns() == []
    else:
        assert observed.game_state == (GameState.MISSION_COMPLETE if outcome == "complete" else GameState.MISSION_FAILED)
        assert len(checkpoints[-1][1]) == 1
        assert checkpoints[-1][1][0]["success"] is (outcome == "complete")
