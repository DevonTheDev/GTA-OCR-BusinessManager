"""Explicit native live-preview decisions over fixed saved check-in drafts."""

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import os

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for native live check-in preview tests', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, QTimer, Qt
from PyQt6.QtTest import QTest

from src.ui.widgets.business_checkin_editor import BusinessCheckInEditor
from src.ui.widgets.business_checkins_dialog import BusinessCheckInsDialog
from tests.test_business_checkins_qt import (
    choose_business, clean_checkin_widgets as clean_checkin_widgets,
    records as records,
)


@pytest.fixture
def qt(native_qt_application):
    assert native_qt_application is not None
    return native_qt_application


NOTE = ' \tPersonal <b>雪😀</b>\nkeep\u00a0space\n  '
CAPTURED = datetime(2026, 10, 4, 11, 12, 13, 123456, tzinfo=timezone.utc)


def reading(**changes):
    from src.game.live_business_snapshot import LiveBusinessReadingSnapshot
    fields = dict(
        business_id='bunker', stock_percent=0, supply_percent=None,
        stock_value=9223372036854775807,
        updated_at=datetime(2026, 10, 4, 10, 2, 3),
        identity_source='manual_entry', captured_at=CAPTURED, uses_supplies=True,
    )
    fields.update(changes)
    return LiveBusinessReadingSnapshot(**fields)


def editor_for(records, qt, provider=None, **kwargs):
    repo, alpha, _ = records
    editor = BusinessCheckInEditor(
        repo, alpha, kwargs.pop('business_id', 'bunker'),
        character_name=kwargs.pop('character_name', 'Alpha <b>literal</b>'),
        live_reading_provider=provider, **kwargs,
    )
    editor.show()
    qt.processEvents()
    return editor


def draft(editor):
    editor._stock_edit.setText('invalid stock')
    editor._supply_edit.setText(' 91 ')
    editor._value_edit.setText('-5')
    editor._note_edit.setPlainText(NOTE)
    return editor._draft()


def preview(editor, qt, action):
    """Drive a real modal loop while propagating callback assertion failures."""
    errors = []
    children = []

    def interact():
        child = editor._live_preview
        children.append(child)
        try:
            assert child is not None and child.isVisible() and child.isModal()
            action(child)
        except BaseException as exc:
            errors.append(exc)
        finally:
            if child is not None and not sip.isdeleted(child) and child.isVisible():
                child.reject()

    QTimer.singleShot(0, interact)
    editor._preview_live_button.click()
    qt.processEvents()
    if errors:
        raise errors[0]
    assert children, 'the explicit action must open its modal preview'
    assert editor._live_preview is None
    assert not editor._busy
    return children[0]


def test_standalone_editor_disables_only_live_preview(records, qt):
    repo, alpha, _ = records
    editor = BusinessCheckInEditor(repo, alpha, 'bunker')
    editor.show()
    qt.processEvents()
    assert hasattr(editor, '_preview_live_button'), 'manual editor needs an explicit live preview action'
    assert not editor._preview_live_button.isEnabled()
    assert editor._save_button.isEnabled() and editor._stock_edit.isEnabled()
    editor._note_edit.setPlainText('Standalone manual draft')
    editor._save_button.click()
    assert repo.get_business_checkin_history(alpha, 'bunker').rows[0].note == 'Standalone manual draft'


def test_opening_board_and_editor_never_reads_live_provider(records, qt):
    repo, alpha, _ = records
    calls = []
    board = BusinessCheckInsDialog(repo, character_id=alpha, live_reading_provider=lambda business: calls.append(business))
    board.show()
    choose_business(board)
    board._record_button.click()
    qt.processEvents()
    assert board._editor._preview_live_button.isEnabled()
    assert calls == []
    board.close()


def test_explicit_use_replaces_unknowns_zero_and_max_without_touching_note_or_saving(records, qt):
    repo, alpha, _ = records
    calls = []
    snapshot = reading()
    editor = editor_for(records, qt, lambda business: (calls.append(business), snapshot)[1])
    draft(editor)
    note_changes = []
    editor._note_edit.textChanged.connect(lambda: note_changes.append(True))

    def use(child):
        assert calls == ['bunker']
        assert '<b>literal</b>' in child._context_label.text()
        assert f'#{alpha}' in child._context_label.text() and 'bunker' in child._context_label.text()
        assert child._stock_label.text() == '0%'
        assert child._supply_label.text() == 'Unknown'
        assert child._value_label.text() == '$9223372036854775807'
        assert 'local time' in child._updated_label.text()
        assert 'UTC' in child._captured_label.text()
        assert 'not character-tagged' in child._help_label.text()
        assert 'personal note' in child._help_label.text().lower()
        assert editor._draft() == ('invalid stock', ' 91 ', '-5', NOTE)
        assert repo.get_business_checkin_history(alpha, 'bunker').total == 0
        child._use_button.click()

    child = preview(editor, qt, use)
    assert editor._draft() == ('0', '', '9223372036854775807', NOTE)
    assert note_changes == []
    assert calls == ['bunker']
    assert repo.get_business_checkin_history(alpha, 'bunker').total == 0
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert sip.isdeleted(child)


@pytest.mark.parametrize('decision', ['cancel', 'escape', 'close'])
def test_dismiss_preview_preserves_complete_invalid_draft(records, qt, decision):
    editor = editor_for(records, qt, lambda _: reading())
    before = draft(editor)

    def dismiss(child):
        if decision == 'cancel':
            child._cancel_button.click()
        elif decision == 'escape':
            QTest.keyClick(child, Qt.Key.Key_Escape)
        else:
            assert child.close()

    preview(editor, qt, dismiss)
    assert editor._draft() == before
    assert editor._preview_live_button.isEnabled() and editor._save_button.isEnabled()


@pytest.mark.parametrize('source,expected', [
    ('manual_entry', 'Manual live entry'), ('ocr_text', 'OCR text match'),
    ('selected_target', 'OCR with selected business target'), (None, 'Source not recorded'),
])
def test_preview_source_and_aware_time_preserve_provenance(records, qt, source, expected):
    observed = datetime(2026, 10, 3, 21, 22, 23, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    editor = editor_for(records, qt, lambda _: reading(identity_source=source, updated_at=observed))

    def inspect(child):
        assert child._source_label.text() == expected
        assert '2026-10-03 21:22:23+05:30' in child._updated_label.text()
        assert 'local time' not in child._updated_label.text()
        child._cancel_button.click()

    preview(editor, qt, inspect)


def test_unsupported_supply_is_na_and_clears_previous_draft_supply(records, qt):
    editor = editor_for(records, qt, lambda _: reading(business_id='nightclub', uses_supplies=False, updated_at=None), business_id='nightclub')
    draft(editor)

    def use(child):
        assert child._supply_label.text() == 'N/A'
        assert child._updated_label.text() == 'Unknown'
        child._use_button.click()

    preview(editor, qt, use)
    assert editor._draft() == ('0', '', '9223372036854775807', NOTE)


def test_unknown_stock_and_value_replace_existing_measurements(records, qt):
    editor = editor_for(records, qt, lambda _: reading(stock_percent=None, supply_percent=0, stock_value=None))
    draft(editor)

    def use(child):
        assert child._stock_label.text() == child._value_label.text() == 'Unknown'
        assert child._supply_label.text() == '0%'
        child._use_button.click()

    preview(editor, qt, use)
    assert editor._draft() == ('', '0', '', NOTE)


@pytest.mark.parametrize('invalid', [
    None, {'stock': 3}, 'bad business', 'bad source', 'bad flag', 'bad updated',
    'naive capture', 'offset capture', 'bool', 'negative', 'overflow', 'float', 'empty', 'nonbool flag',
])
def test_missing_and_malformed_provider_results_preserve_draft_and_allow_retry(records, qt, invalid):
    payloads = {
        'bad business': lambda: reading(business_id='meth'),
        'bad source': lambda: reading(identity_source='SECRET_SOURCE'),
        'bad flag': lambda: reading(uses_supplies=False),
        'nonbool flag': lambda: reading(uses_supplies=1),
        'bad updated': lambda: reading(updated_at='SECRET_TIMESTAMP'),
        'naive capture': lambda: reading(captured_at=CAPTURED.replace(tzinfo=None)),
        'offset capture': lambda: reading(captured_at=CAPTURED.astimezone(timezone(timedelta(hours=1)))),
        'bool': lambda: reading(stock_percent=True),
        'negative': lambda: reading(supply_percent=-1),
        'overflow': lambda: reading(stock_value=2**63),
        'float': lambda: reading(stock_percent=1.0),
        'empty': lambda: reading(stock_percent=None, stock_value=None),
    }
    state = [payloads[invalid]() if isinstance(invalid, str) else invalid]
    editor = editor_for(records, qt, lambda _: state[0])
    before = draft(editor)
    editor._preview_live_button.click()
    assert editor._draft() == before
    assert editor._live_preview is None and not editor._busy
    assert editor._preview_live_button.isEnabled()
    assert 'SECRET' not in editor._status_label.text()
    assert 'draft' in editor._status_label.text().lower()
    state[0] = reading()
    preview(editor, qt, lambda child: child._use_button.click())
    assert editor._draft() == ('0', '', '9223372036854775807', NOTE)


def test_unsupported_supply_payload_is_rejected_without_silent_normalization(records, qt):
    editor = editor_for(
        records, qt, lambda _: reading(business_id='nightclub', uses_supplies=False, supply_percent=50),
        business_id='nightclub',
    )
    before = draft(editor)
    editor._preview_live_button.click()
    assert editor._draft() == before and editor._live_preview is None and not editor._busy


def test_snapshot_subclass_is_not_an_accepted_provider_contract(records, qt):
    from src.game.live_business_snapshot import LiveBusinessReadingSnapshot

    class OtherSnapshot(LiveBusinessReadingSnapshot):
        pass

    snapshot = reading()
    supplied = OtherSnapshot(**vars(snapshot))
    editor = editor_for(records, qt, lambda _: supplied)
    before = draft(editor)
    editor._preview_live_button.click()
    assert editor._draft() == before and editor._live_preview is None and not editor._busy


def test_provider_failure_is_bounded_and_retryable(records, qt):
    calls = []

    def provider(_):
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError('SECRET_PATH_' + 'x' * 10000)
        return reading()

    editor = editor_for(records, qt, provider)
    before = draft(editor)
    editor._preview_live_button.click()
    assert editor._draft() == before and not editor._busy
    assert 'SECRET' not in editor._status_label.text() and len(editor._status_label.text()) < 250
    preview(editor, qt, lambda child: child._use_button.click())
    assert len(calls) == 2


def test_modal_failure_releases_ownership_preserves_draft_and_retires_preview(records, qt, monkeypatch):
    import src.ui.widgets.business_checkin_live_preview as module
    editor = editor_for(records, qt, lambda _: reading())
    before = draft(editor)
    failed = []

    def fail(child):
        failed.append(child)
        assert editor._busy and editor._live_preview is child
        raise RuntimeError('SECRET_EXEC_FAILURE')

    with monkeypatch.context() as patch:
        patch.setattr(module.BusinessCheckInLivePreview, 'exec', fail)
        editor._preview_live_button.click()
    assert len(failed) == 1 and editor._draft() == before
    assert not editor._busy and editor._live_preview is None
    assert 'SECRET' not in editor._status_label.text()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert sip.isdeleted(failed[0])
    preview(editor, qt, lambda child: child._use_button.click())


def test_copy_then_save_records_new_save_time_with_exact_original_note(records, qt, monkeypatch):
    import src.database.repository as repository_module
    repo, alpha, _ = records
    editor = editor_for(records, qt, lambda _: reading())
    draft(editor)
    preview(editor, qt, lambda child: child._use_button.click())
    assert repo.get_business_checkin_history(alpha, 'bunker').total == 0
    saved_at = CAPTURED + timedelta(hours=2)
    monkeypatch.setattr(repository_module, 'utc_now', lambda: saved_at)
    editor._save_button.click()
    row = repo.get_business_checkin_history(alpha, 'bunker').rows[0]
    assert row.recorded_at == saved_at and row.recorded_at != CAPTURED
    assert (row.stock_percent, row.supply_percent, row.stock_value, row.note) == (
        0, None, 9223372036854775807, NOTE,
    )
    assert 'identity_source' not in asdict(row) and 'captured_at' not in asdict(row)


def test_interrupted_copy_restores_numeric_draft_and_never_touches_note(records, qt, monkeypatch):
    editor = editor_for(records, qt, lambda _: reading())
    before = draft(editor)
    note_changes = []
    editor._note_edit.textChanged.connect(lambda: note_changes.append(True))

    def fail(_):
        raise RuntimeError('SECRET_SET_TEXT_FAILURE')

    with monkeypatch.context() as patch:
        patch.setattr(editor._supply_edit, 'setText', fail)
        preview(editor, qt, lambda child: child._use_button.click())
    assert editor._draft() == before
    assert note_changes == []
    assert 'unchanged' in editor._status_label.text()
    assert 'SECRET' not in editor._status_label.text()
    preview(editor, qt, lambda child: child._use_button.click())
    assert editor._draft() == ('0', '', '9223372036854775807', NOTE)


def test_interrupted_copy_and_failed_restore_never_claim_unchanged(records, qt, monkeypatch):
    editor = editor_for(records, qt, lambda _: reading())
    draft(editor)
    original = editor._stock_edit.setText

    def stock_once(value):
        if value != '0':
            raise RuntimeError('SECRET_RESTORE_FAILURE')
        original(value)

    def fail(_):
        raise RuntimeError('SECRET_SET_TEXT_FAILURE')

    with monkeypatch.context() as patch:
        patch.setattr(editor._stock_edit, 'setText', stock_once)
        patch.setattr(editor._supply_edit, 'setText', fail)
        preview(editor, qt, lambda child: child._use_button.click())
    assert editor._draft() == ('0', ' 91 ', '-5', NOTE)
    assert 'draft is unchanged' not in editor._status_label.text().lower()
    assert 'review' in editor._status_label.text().lower()
    assert 'SECRET' not in editor._status_label.text()


def test_preview_owns_provider_modal_and_acceptance_against_reentry_and_parent_close(records, qt):
    repo, alpha, beta = records
    calls = []
    board = None

    def attempt_actions():
        editor = board._editor
        assert editor._busy and not editor._save_button.isEnabled()
        editor._preview_live_values()
        editor._save()
        editor.accept()
        editor.reject()
        assert not editor.close()
        board.reject()
        assert not board.close()
        assert not editor._closed and not board._closed
        assert repo.get_business_checkin_history(alpha, 'bunker').total == 0

    def provider(business):
        calls.append(business)
        attempt_actions()
        return reading()

    board = BusinessCheckInsDialog(repo, character_id=alpha, live_reading_provider=provider)
    board.show()
    choose_business(board)
    board._record_button.click()
    editor = board._editor
    draft(editor)
    editor._stock_edit.textChanged.connect(attempt_actions)

    def use(child):
        attempt_actions()
        board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
        choose_business(board, 'meth')
        board._record_button.click()
        assert board._editor is editor
        child._use_button.click()
        assert editor._busy and editor._live_preview is child
        assert not sip.isdeleted(child)

    preview(editor, qt, use)
    assert calls == ['bunker']
    assert editor._character_id == alpha and editor._business_id == 'bunker'
    assert editor._draft() == ('0', '', '9223372036854775807', NOTE)


def test_accepted_values_stay_captured_when_provider_live_value_changes(records, qt):
    live = [reading(stock_percent=15)]
    calls = []
    editor = editor_for(records, qt, lambda bid: (calls.append(bid), live[0])[1])
    draft(editor)

    def use(child):
        live[0] = replace(live[0], stock_percent=99, supply_percent=80, stock_value=1)
        assert child._stock_label.text() == '15%'
        child._use_button.click()

    preview(editor, qt, use)
    assert calls == ['bunker']
    assert editor._draft() == ('15', '', '9223372036854775807', NOTE)


def test_long_literal_context_keeps_action_controls_reachable(records, qt, monkeypatch):
    import src.ui.widgets.business_checkin_live_preview as module
    name = '<b>literal</b>雪😀' * 180 + '\nSaved character'
    business = '<a href="file:///private">literal business</a>' * 80
    monkeypatch.setattr(module, 'business_label', lambda _: business)
    editor = editor_for(records, qt, lambda _: reading(stock_percent=100, supply_percent=100), character_name=name)

    def inspect(child):
        child.resize(420, 320)
        qt.processEvents()
        assert name in child._context_label.text() and business in child._context_label.text()
        assert child._context_label.textFormat() == Qt.TextFormat.PlainText
        assert child._stock_label.text() == child._supply_label.text() == '100%'
        assert child._scroll_area.verticalScrollBar().maximum() > 0
        for button in (child._use_button, child._cancel_button):
            assert button.isEnabled() and button.isVisible()
            assert child.rect().contains(button.mapTo(child, QPoint(0, 0)))
            assert child.rect().contains(button.mapTo(child, button.rect().bottomRight()))
        child._cancel_button.click()

    preview(editor, qt, inspect)
