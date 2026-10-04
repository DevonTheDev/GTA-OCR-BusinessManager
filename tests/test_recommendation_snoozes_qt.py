"""Native recommendation actions, press ownership and MainWindow layout."""

import os
import sqlite3
from types import SimpleNamespace

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for native recommendation controls", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QMetaObject, Qt, QTimer
from PyQt6.QtTest import QTest

from src.app import GTABusinessManager
from src.config import settings as settings_module
from src.config.settings import Settings
from src.database.repository import Repository
from src.optimization.optimizer import Recommendation
from src.ui.widgets.recommendations import RecommendationsPanel


def recommendation(key, action=None):
    return Recommendation(1, action or f"Action {key}", "A useful reason", 10000, 5,
                          recommendation_id=key)


class CandidateApp:
    """Deterministic UI boundary to change candidates during a native Qt press."""

    def __init__(self, candidates):
        self.candidates = list(candidates)
        self.snoozes = set()
        self.calls = []
        self.restore_calls = 0

    @property
    def recommendation_snapshot(self):
        visible = [rec for rec in self.candidates
                   if getattr(rec, "recommendation_id", None) not in self.snoozes]
        return SimpleNamespace(visible=tuple(visible[:7]), total_candidates=len(self.candidates),
                               hidden_count=len(self.candidates) - len(visible),
                               snoozed_count=len(self.snoozes))

    @property
    def recommendations(self):
        return list(self.recommendation_snapshot.visible)

    def snooze_recommendation(self, key):
        self.calls.append(key)
        if key in self.snoozes or key not in {rec.recommendation_id for rec in self.candidates}:
            return False
        self.snoozes.add(key)
        return True

    def restore_snoozed_recommendations(self):
        self.restore_calls += 1
        self.snoozes.clear()


@pytest.fixture
def qt(native_qt_application):
    assert native_qt_application is not None
    return native_qt_application


@pytest.fixture
def widgets(qt):
    created = []

    def create(cls, *args):
        widget = cls(*args)
        created.append(widget)
        if hasattr(widget, "_timer"):
            widget._timer.stop()
        widget.resize(850, 550)
        widget.show()
        qt.processEvents()
        return widget

    yield create
    for widget in created:
        if not sip.isdeleted(widget):
            widget.hide()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def snooze(card):
    buttons = [button for button in card.findChildren(QtWidgets.QPushButton)
               if button.text() == "Snooze 10 min" and not button.isHidden()]
    assert len(buttons) == 1, "Each recommendation needs one Snooze 10 min action"
    return buttons[0]


def restore(panel):
    buttons = [button for button in panel.findChildren(QtWidgets.QPushButton)
               if button.text() == "Restore all" and not button.isHidden()]
    assert len(buttons) == 1, "Recommendation snoozes need persistent Restore all access"
    return buttons[0]


def actions(panel):
    return [card._action_label.text() for card in panel._cards if not card.isHidden()]


def test_constructor_populates_five_cards_without_waiting_for_timer(widgets):
    app = CandidateApp([recommendation(str(i)) for i in range(9)])
    panel = widgets(RecommendationsPanel, app)
    assert actions(panel) == [f"Action {i}" for i in range(5)]
    assert panel._timer.parent() is panel
    assert not restore(panel).isEnabled()


def test_snooze_refills_immediately_and_restore_has_one_connection(widgets):
    app = CandidateApp([recommendation(str(i)) for i in range(9)])
    panel = widgets(RecommendationsPanel, app)
    for _ in range(10):
        panel._update_display()
    for i in range(9):
        snooze(panel._cards[0]).click()
        assert app.calls == [str(j) for j in range(i + 1)]
        assert actions(panel) == [f"Action {j}" for j in range(i + 1, min(i + 6, 9))]
    assert panel._empty_label.isVisible()
    assert "snoozed" in panel._empty_label.text().lower()
    assert restore(panel).isVisible() and restore(panel).isEnabled()
    assert "9" in panel._snooze_status_label.text()
    restore(panel).click()
    assert app.restore_calls == 1 and not app.snoozes
    assert actions(panel) == [f"Action {i}" for i in range(5)]
    assert not restore(panel).isEnabled()


def test_empty_candidates_keep_restore_for_stored_snoozes(widgets):
    app = CandidateApp([recommendation("a")])
    panel = widgets(RecommendationsPanel, app)
    snooze(panel._cards[0]).click()
    assert "snoozed" in panel._empty_label.text().lower()
    app.candidates = []
    panel._update_display()
    assert "No recommendations" in panel._empty_label.text()
    assert "all" not in panel._empty_label.text().lower()
    assert restore(panel).isEnabled()
    assert "1" in panel._snooze_status_label.text()
    restore(panel).click()
    assert app.snoozes == set() and not restore(panel).isEnabled()


@pytest.mark.parametrize("key", [None, "", "   ", 7])
def test_idless_recommendation_is_readable_but_not_actionable(widgets, key):
    app = CandidateApp([recommendation(key, "Legacy recommendation")])
    panel = widgets(RecommendationsPanel, app)
    assert actions(panel) == ["Legacy recommendation"]
    assert not snooze(panel._cards[0]).isEnabled()
    snooze(panel._cards[0]).click()
    assert not app.calls


def test_legacy_record_without_identity_attribute_remains_readable(widgets):
    rec = SimpleNamespace(priority=2, action="Legacy action", reason="External source",
                          estimated_value=0, estimated_time_minutes=0)
    panel = widgets(RecommendationsPanel, CandidateApp([rec]))
    assert actions(panel) == ["Legacy action"]
    assert not snooze(panel._cards[0]).isEnabled()


@pytest.mark.parametrize("method", ["click", "animateClick"])
def test_native_button_slot_can_activate_current_binding(widgets, method):
    app = CandidateApp([recommendation("a")])
    panel = widgets(RecommendationsPanel, app)
    QMetaObject.invokeMethod(snooze(panel._cards[0]), method, Qt.ConnectionType.DirectConnection)
    if method == "animateClick":
        QTest.qWait(150)
    assert app.calls == ["a"]


def test_delayed_native_animation_cannot_activate_replacement(widgets):
    app = CandidateApp([recommendation("a")])
    panel = widgets(RecommendationsPanel, app)
    button = snooze(panel._cards[0])
    QMetaObject.invokeMethod(button, "animateClick", Qt.ConnectionType.DirectConnection)
    app.candidates = [recommendation("b")]
    panel._update_display()
    QTest.qWait(150)
    assert not app.calls


@pytest.mark.parametrize("press", ["space", "mouse"])
def test_retired_animation_cannot_consume_a_new_physical_press(widgets, press):
    app = CandidateApp([recommendation("a")])
    panel = widgets(RecommendationsPanel, app)
    button = snooze(panel._cards[0])
    QMetaObject.invokeMethod(button, "animateClick", Qt.ConnectionType.DirectConnection)
    app.candidates = [recommendation("b")]
    panel._update_display()
    button = snooze(panel._cards[0])
    if press == "space":
        QTest.keyPress(button, Qt.Key.Key_Space)
    else:
        QTest.mousePress(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
    QTest.qWait(150)
    assert not app.calls, "The old animation must not release a newer user's press"
    if press == "space":
        QTest.keyRelease(button, Qt.Key.Key_Space)
    else:
        QTest.mouseRelease(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
    assert app.calls == ["b"]


def test_binding_change_retires_native_control_and_transfers_focus(widgets, qt):
    app = CandidateApp([recommendation("a")])
    panel = widgets(RecommendationsPanel, app)
    previous = snooze(panel._cards[0])
    previous.setFocus()
    app.candidates = [recommendation("b")]
    panel._update_display()
    current = snooze(panel._cards[0])
    assert current is not previous
    assert previous.isHidden() and not previous.isEnabled()
    assert current.hasFocus()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt.processEvents()
    assert sip.isdeleted(previous)
    QTest.keyClick(current, Qt.Key.Key_Space)
    assert app.calls == ["b"]


@pytest.mark.parametrize("replacement", ["middle_card", "restore"])
@pytest.mark.parametrize("reverse", [False, True])
def test_replacement_preserves_forward_and_reverse_keyboard_order(widgets, qt, replacement, reverse):
    app = CandidateApp([recommendation(key) for key in "abcde"])
    app.snoozes.add("temporarily absent")
    host = widgets(QtWidgets.QWidget)
    layout = QtWidgets.QVBoxLayout(host)
    before = QtWidgets.QPushButton("Before recommendations", host)
    layout.addWidget(before)
    panel = RecommendationsPanel(app, host)
    panel._timer.stop()
    layout.addWidget(panel)
    after = QtWidgets.QPushButton("After recommendations", host)
    layout.addWidget(after)
    qt.processEvents()
    if replacement == "middle_card":
        previous = snooze(panel._cards[2])
        app.candidates[2] = recommendation("x")
        panel._update_display()
        assert snooze(panel._cards[2]) is not previous
    else:
        previous = restore(panel)
        app.snoozes.clear()
        panel._update_display()
        app.snoozes.add("temporarily absent")
        panel._update_display()
        assert restore(panel) is not previous
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt.processEvents()
    assert sip.isdeleted(previous)
    ordered = [before, restore(panel), *[snooze(card) for card in panel._cards], after]
    if reverse:
        ordered.reverse()
    ordered[0].setFocus()
    qt.processEvents()
    assert qt.focusWidget() is ordered[0]
    for expected in ordered[1:]:
        # The scroll viewport may also be a Tab stop. Observe each actual button,
        # including controls outside the panel, rather than reading focus-chain internals.
        for _ in range(20):
            QTest.keyClick(qt.focusWidget(), Qt.Key.Key_Tab,
                           Qt.KeyboardModifier.ShiftModifier if reverse
                           else Qt.KeyboardModifier.NoModifier)
            qt.processEvents()
            current = qt.focusWidget()
            if isinstance(current, QtWidgets.QPushButton):
                break
        assert current is expected


def test_rejected_snooze_has_truthful_retry_and_restore_guidance(widgets, monkeypatch):
    app = CandidateApp([recommendation("a")])
    panel = widgets(RecommendationsPanel, app)
    monkeypatch.setattr(app, "snooze_recommendation", lambda _key: False)
    snooze(panel._cards[0]).click()
    status = panel._action_status_label.text()
    assert "no longer available" not in status
    assert "Restore all" in status and "try again" in status.lower()
    assert actions(panel) == ["Action a"]


@pytest.mark.parametrize("press", ["space", "mouse"])
@pytest.mark.parametrize("rebind", ["different", "aba", "retired", "owner", "owner_without_refresh"])
def test_pending_press_cannot_snooze_rebound_recommendation(widgets, qt, press, rebind):
    original = CandidateApp([recommendation("a")])
    replacement = CandidateApp([recommendation("a")])
    panel = widgets(RecommendationsPanel, original)
    button = snooze(panel._cards[0])
    button.setFocus()
    if press == "space":
        QTest.keyPress(button, Qt.Key.Key_Space)
    else:
        QTest.mousePress(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
    assert button.isDown()
    if rebind in {"different", "aba"}:
        original.candidates = [recommendation("b")]
        panel._update_display()
        if rebind == "aba":
            original.candidates = [recommendation("a")]
            panel._update_display()
    elif rebind == "retired":
        original.candidates = []
        panel._update_display()
    else:
        panel._app = replacement
        if rebind == "owner":
            panel._update_display()
    if press == "space":
        QTest.keyRelease(button, Qt.Key.Key_Space)
    else:
        QTest.mouseRelease(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
    qt.processEvents()
    assert not original.calls and not replacement.calls
    if rebind != "retired":
        panel._update_display()
        button = snooze(panel._cards[0])
        QTest.keyClick(button, Qt.Key.Key_Space)
        current = replacement if rebind.startswith("owner") else original
        assert current.calls == [current.candidates[0].recommendation_id]


def test_unchanged_refresh_during_space_press_preserves_intended_action(widgets):
    app = CandidateApp([recommendation("a")])
    panel = widgets(RecommendationsPanel, app)
    button = snooze(panel._cards[0])
    QTest.keyPress(button, Qt.Key.Key_Space)
    app.candidates = [recommendation("a", "Updated amount, same action")]
    panel._update_display()
    assert snooze(panel._cards[0]) is button
    QTest.keyRelease(button, Qt.Key.Key_Space)
    assert app.calls == ["a"]


def test_retired_signals_and_no_app_cannot_mutate_prior_or_new_manager(widgets):
    first = CandidateApp([recommendation("a")])
    second = CandidateApp([recommendation("a")])
    panel = widgets(RecommendationsPanel, first)
    button = snooze(panel._cards[0])
    panel._app = None
    panel._update_display()
    assert not actions(panel) and not button.isEnabled()
    button.click()
    button.clicked.emit()
    assert not first.calls
    panel._app = second
    panel._update_display()
    # A queued clicked signal has no press/programmatic activation ownership.
    button.clicked.emit()
    assert not first.calls and not second.calls
    snooze(panel._cards[0]).click()
    assert second.calls == ["a"] and not first.calls


@pytest.mark.parametrize("rebind", ["aba", "owner", "retired"])
def test_captured_callback_cannot_outlive_its_binding(widgets, rebind):
    first = CandidateApp([recommendation("a")])
    second = CandidateApp([recommendation("a")])
    panel = widgets(RecommendationsPanel, first)
    button = snooze(panel._cards[0])
    callback = button._callback
    if rebind == "aba":
        first.candidates = [recommendation("b")]
        panel._update_display()
        first.candidates = [recommendation("a")]
    elif rebind == "owner":
        panel._app = second
    else:
        first.candidates = []
    panel._update_display()
    callback()
    assert not first.calls and not second.calls


def test_restore_pending_press_cannot_mutate_replacement_manager(widgets):
    first = CandidateApp([recommendation("a")])
    second = CandidateApp([recommendation("b")])
    first.snoozes.add("a")
    second.snoozes.add("b")
    panel = widgets(RecommendationsPanel, first)
    button = restore(panel)
    QTest.keyPress(button, Qt.Key.Key_Space)
    panel._app = second
    panel._update_display()
    QTest.keyRelease(button, Qt.Key.Key_Space)
    assert first.snoozes == {"a"} and second.snoozes == {"b"}
    restore(panel).click()
    assert second.restore_calls == 1 and first.restore_calls == 0


def test_retired_restore_animation_cannot_consume_new_owner_press(widgets):
    first = CandidateApp([recommendation("a")])
    second = CandidateApp([recommendation("b")])
    first.snoozes.add("a")
    second.snoozes.add("b")
    panel = widgets(RecommendationsPanel, first)
    previous = restore(panel)
    QMetaObject.invokeMethod(previous, "animateClick", Qt.ConnectionType.DirectConnection)
    panel._app = second
    panel._update_display()
    current = restore(panel)
    assert current is not previous
    QTest.keyPress(current, Qt.Key.Key_Space)
    QTest.qWait(150)
    assert first.restore_calls == 0 and second.restore_calls == 0
    QTest.keyRelease(current, Qt.Key.Key_Space)
    assert first.restore_calls == 0 and second.restore_calls == 1


def test_buttons_are_keyboard_accessible_and_labels_treat_markup_literally(widgets):
    rec = recommendation("a", "<b>Sell Bunker</b>")
    rec.reason = "<img src='never'> Keep this literal"
    app = CandidateApp([rec])
    panel = widgets(RecommendationsPanel, app)
    button = snooze(panel._cards[0])
    assert "<b>Sell Bunker</b>" in button.accessibleName()
    assert "10 min" in button.accessibleName()
    assert button.focusPolicy() & Qt.FocusPolicy.TabFocus
    for label in panel.findChildren(QtWidgets.QLabel):
        assert label.textFormat() == Qt.TextFormat.PlainText
    assert panel._cards[0]._action_label.text() == rec.action
    assert panel._cards[0]._reason_label.text() == rec.reason
    info = " ".join(label.text() for label in panel.findChildren(QtWidgets.QLabel)).lower()
    assert "app-wide" in info and "not saved" in info and "10 min" in info
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert app.calls == ["a"]
    assert "Restore" in restore(panel).accessibleName()
    assert restore(panel).focusPolicy() & Qt.FocusPolicy.TabFocus
    QTest.keyClick(restore(panel), Qt.Key.Key_Space)
    assert app.restore_calls == 1


def test_panel_destruction_deletes_child_timer(widgets, qt):
    panel = widgets(RecommendationsPanel, CandidateApp([]))
    timer = panel._timer
    assert timer.parent() is panel
    timer.start(1)
    panel.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt.processEvents()
    assert sip.isdeleted(panel) and sip.isdeleted(timer)


@pytest.fixture
def real_window(tmp_path, monkeypatch, qt):
    from src.ui.main_window import MainWindow

    settings = Settings(tmp_path / "recommendations.yaml")
    monkeypatch.setattr(settings_module, "_settings", settings)
    repository = Repository(str(tmp_path / "recommendations.db"))
    assert repository.initialize()
    repository.get_or_create_character("Local test")
    manager = GTABusinessManager(settings)
    manager._repository = repository
    for key in ("bunker", "acid_lab", "cocaine", "meth", "nightclub"):
        manager.update_business_state(key, 100, 0, 123456)
    window = MainWindow(manager)
    window._tabs.setCurrentWidget(window._recommendations)
    window.show()
    qt.processEvents()
    yield manager, window, repository
    manager.stop()
    for timer in window.findChildren(QTimer):
        timer.stop()
    # Retire the existing unrelated parentless business timer, too.
    window._business_panel._timer.stop()
    window._business_panel._timer.deleteLater()
    window._dashboard._timer.stop()
    window._dashboard._timer.deleteLater()
    window.hide()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    repository.close()
    repository._session_factory.kw["bind"].dispose()


def test_failed_refresh_retires_actions_and_hides_exception_details(widgets, monkeypatch, caplog):
    app = CandidateApp([recommendation("a")])
    panel = widgets(RecommendationsPanel, app)
    button = snooze(panel._cards[0])

    def fail(_self):
        raise RuntimeError("private <b>database details</b>")

    monkeypatch.setattr(CandidateApp, "recommendation_snapshot", property(fail))
    panel._update_display()
    assert not actions(panel) and not button.isEnabled() and not restore(panel).isEnabled()
    assert "could not" in panel._empty_label.text().lower()
    assert "private" not in " ".join(label.text() for label in panel.findChildren(QtWidgets.QLabel))
    assert "private" not in caplog.text
    button.click()
    assert not app.calls


@pytest.mark.parametrize("operation", ["snooze", "restore"])
def test_success_then_failed_refresh_preserves_actual_outcome(widgets, monkeypatch, operation):
    app = CandidateApp([recommendation("a")])
    if operation == "restore":
        app.snoozes.add("a")
    panel = widgets(RecommendationsPanel, app)

    def fail(_self):
        raise RuntimeError("private refresh details")

    monkeypatch.setattr(CandidateApp, "recommendation_snapshot", property(fail))
    (snooze(panel._cards[0]) if operation == "snooze" else restore(panel)).click()
    if operation == "snooze":
        assert app.snoozes == {"a"}
        assert "Snoozed for 10 min" in panel._action_status_label.text()
    else:
        assert not app.snoozes
        assert "restored" in panel._action_status_label.text().lower()
    assert "could not" in panel._empty_label.text().lower()
    assert not actions(panel)


@pytest.mark.parametrize("operation", ["snooze", "restore"])
def test_failed_action_is_bounded_and_can_be_retried(widgets, monkeypatch, caplog, operation):
    app = CandidateApp([recommendation("a")])
    if operation == "restore":
        app.snoozes.add("a")
    panel = widgets(RecommendationsPanel, app)
    method = "snooze_recommendation" if operation == "snooze" else "restore_snoozed_recommendations"
    original = getattr(app, method)

    def fail(*_args):
        raise RuntimeError("private action details")

    monkeypatch.setattr(app, method, fail)
    button = snooze(panel._cards[0]) if operation == "snooze" else restore(panel)
    button.click()
    assert "could not" in panel._action_status_label.text().lower()
    assert "private" not in panel._action_status_label.text() and "private" not in caplog.text
    monkeypatch.setattr(app, method, original)
    button.click()
    assert (app.calls == ["a"]) if operation == "snooze" else (app.restore_calls == 1)


def test_actual_manager_snooze_updates_all_recommendations_without_saving(real_window):
    manager, window, repository = real_window
    panel = window._recommendations
    with sqlite3.connect(repository._db_path) as database:
        before = tuple(database.iterdump())
    settings_before = manager._settings._config_path.read_bytes()
    initial = manager.recommendations
    assert len(initial) >= 5
    selected = initial[0].recommendation_id
    snooze(panel._cards[0]).click()
    assert selected not in {rec.recommendation_id for rec in manager.recommendations}
    assert manager.recommendation_snapshot.snoozed_count == 1
    assert panel._cards[0]._action_label.text() == manager.recommendations[0].action
    restore(panel).click()
    assert [rec.recommendation_id for rec in manager.recommendations] == [
        rec.recommendation_id for rec in initial]
    with sqlite3.connect(repository._db_path) as database:
        assert tuple(database.iterdump()) == before
    assert manager._settings._config_path.read_bytes() == settings_before


def test_actual_dashboard_and_overlay_clear_snoozed_actions_and_restore(real_window, qt):
    from src.ui.overlay import OverlayWindow

    manager, window, _repository = real_window
    panel, dashboard = window._recommendations, window._dashboard
    overlay = OverlayWindow(manager)
    overlay.show()
    try:
        dashboard._update_display()
        overlay._update_ui()
        initial = manager.recommendations[0].action
        assert dashboard._recommendation_card._action_label.text() == initial
        assert overlay._recommendation_label.text() == f"Next: {initial}"
        # Consume each current card, including candidates that refill from below the limit.
        candidate_count = manager.recommendation_snapshot.total_candidates
        for _ in range(candidate_count):
            snooze(panel._cards[0]).click()
        assert manager.recommendations == [] and not actions(panel)
        # The existing consumers pick up the app-wide state on their ordinary refresh path.
        dashboard._update_display()
        overlay._update_ui()
        assert dashboard._recommendation_card._action_label.text() == "No recommendations yet"
        assert not dashboard._recommendation_card._reason_label.text()
        assert not dashboard._recommendation_card._value_label.text()
        assert not overlay._recommendation_label.text() and overlay._recommendation_label.isHidden()
        restore(panel).click()
        dashboard._update_display()
        overlay._update_ui()
        assert dashboard._recommendation_card._action_label.text() == initial
        assert overlay._recommendation_label.text() == f"Next: {initial}"
        assert not overlay._recommendation_label.isHidden()
    finally:
        for timer in overlay.findChildren(QTimer):
            timer.stop()
        overlay.hide()
        overlay.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("width,height", [(900, 650), (1000, 700)])
def test_actual_mainwindow_cards_fit_without_horizontal_scroll(real_window, qt, width, height):
    _manager, window, _repository = real_window
    window.resize(width, height)
    qt.processEvents()
    panel = window._recommendations
    scroll = panel.findChild(QtWidgets.QScrollArea)
    assert (window.width(), window.height()) == (width, height)
    assert scroll.horizontalScrollBar().maximum() == 0
    assert not scroll.horizontalScrollBar().isVisible()
    button = restore(panel)
    assert button.isVisible()
    assert button.mapTo(panel, button.rect().bottomLeft()).y() < scroll.y()
    for card in panel._cards:
        assert not card.isHidden()
        assert card.height() < 220, "Scoped card CSS must not pad/border every nested label"
        assert card.width() <= scroll.viewport().width()
        for label in card.findChildren(QtWidgets.QLabel):
            assert label.contentsRect().width() >= label.width() - 20
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    qt.processEvents()
    assert restore(panel).isVisible() and restore(panel).mapTo(panel, button.rect().topLeft()).y() >= 0
