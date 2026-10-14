"""Batch YAML commits and actual Settings Save methods, without Qt dependencies.

The panel methods are compiled directly from production AST nodes. The adapters
replace only widgets, message boxes, logging and the single-shot timer. These
checks do not exercise native Qt signals, rendering or a running application.
"""

import ast
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

from src.config.defaults import DEFAULT_CONFIG
from src.config.settings import Settings, SettingsValidationError
from src.utils import persistence


class Control:
    def __init__(self, value):
        self.current = value

    def text(self):
        return self.current

    def value(self):
        return self.current

    def isChecked(self):
        return self.current

    def currentIndex(self):
        return self.current

    def setText(self, value):
        self.current = value

    def setEnabled(self, value):
        self.current = value

    def setStyleSheet(self, value):
        self.style = value


def panel_for(settings):
    """Bind unchanged production method ASTs to lightweight widget adapters."""
    source = Path(__file__).resolve().parents[1] / 'src/ui/widgets/settings_panel.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == 'SettingsPanel')
    names = ('_validate_settings', '_save_settings')
    methods = [node for node in cls.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    messages, timers = [], []
    namespace = {
        'QMessageBox': SimpleNamespace(
            warning=lambda owner, title, message: messages.append(('warning', title, message)),
            critical=lambda owner, title, message: messages.append(('critical', title, message))),
        'QTimer': SimpleNamespace(singleShot=lambda delay, callback: timers.append((delay, callback))),
        'logger': SimpleNamespace(info=lambda message: None, error=lambda message: None),
    }
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), namespace)
    panel = SimpleNamespace(_settings=settings, _unsaved_changes=True,
                            _save_btn=Control(True), _status_label=Control('Unsaved draft'),
                            messages=messages, timers=timers)
    values = {
        '_name_edit': '  Batch character  ', '_startup_check': True, '_tray_check': False,
        '_monitor_combo': 2, '_idle_fps': 0.75, '_active_fps': 3.5,
        '_mode_combo': 2, '_pos_combo': 3, '_opacity_slider': 63,
        '_audio_check': True, '_desktop_check': False, '_money_notif_check': False,
        '_overlay_key': '  alt+g  ', '_tracking_key': ' alt+t ', '_window_key': ' alt+m ',
    }
    for name, value in values.items():
        setattr(panel, name, Control(value))
    panel.draft = lambda: {name: getattr(panel, name).current for name in values}
    panel._validate_settings = lambda: namespace['_validate_settings'](panel)
    panel.save = lambda: namespace['_save_settings'](panel)
    return panel


PANEL_PATCH = {
    'general.character_name': 'Batch character', 'general.launch_on_startup': True,
    'general.minimize_to_tray': False, 'capture.monitor_index': 2,
    'capture.idle_fps': 0.75, 'capture.active_fps': 3.5,
    'display.mode': 'both', 'display.overlay_position': 'bottom-right',
    'display.overlay_opacity': 0.63, 'notifications.audio_enabled': True,
    'notifications.desktop_notifications': False, 'notifications.money_notification': False,
    'hotkeys.toggle_overlay': 'alt+g', 'hotkeys.toggle_tracking': 'alt+t',
    'hotkeys.show_window': 'alt+m',
}


def partially_serialize(config, stream, **kwargs):
    stream.write('general:\n  character_name: truncated')
    raise OSError('serializer stopped after partial output')


class SettingsCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / 'settings.yaml'
        self.path.write_text(yaml.safe_dump({
            'general': {'character_name': 'Original'},
            'capture': {'business_fps': 4.5, 'custom_rate_note': 'keep'},
            'custom': {'nested': {'labels': ['keep me']}},
        }), encoding='utf-8')
        self.settings = Settings(self.path)
        self.original = deepcopy(self.settings._config)
        self.original_root = self.settings._config
        self.original_bytes = self.path.read_bytes()
        self.real_replace = persistence.os.replace

    def assert_unchanged(self):
        self.assertIs(self.settings._config, self.original_root)
        self.assertEqual(self.settings._config, self.original)
        self.assertEqual(self.path.read_bytes(), self.original_bytes)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def assert_patch(self, values):
        disk = yaml.safe_load(self.path.read_text(encoding='utf-8'))
        self.assertEqual(disk, self.settings._config)
        for key, expected in values.items():
            self.assertEqual(self.settings.get(key), expected, key)
        self.assertEqual(self.settings.get('custom.nested.labels'), ['keep me'])
        self.assertEqual(self.settings.get('capture.custom_rate_note'), 'keep')
        self.assertEqual(self.settings.get('capture.business_fps'), 4.5)


class TestSettingsPanelSave(SettingsCase):
    def assert_draft_retained(self, panel, draft):
        self.assertEqual(panel.draft(), draft)
        self.assertTrue(panel._unsaved_changes)
        self.assertTrue(panel._save_btn.current)
        self.assertEqual(panel._status_label.current, 'Unsaved draft')
        self.assertEqual(panel.timers, [])

    def test_save_publishes_all_fifteen_fields_with_one_replacement(self):
        panel = panel_for(self.settings)
        draft = panel.draft()
        with patch.object(persistence.os, 'replace', wraps=self.real_replace) as replace:
            panel.save()
        self.assertEqual(replace.call_count, 1)
        self.assert_patch(PANEL_PATCH)
        self.assertEqual(panel.draft(), draft)
        self.assertFalse(panel._unsaved_changes)
        self.assertFalse(panel._save_btn.current)
        self.assertEqual(panel._status_label.current, 'Settings saved!')
        self.assertEqual(panel.messages, [])
        self.assertEqual(len(panel.timers), 1)
        delay, clear_status = panel.timers[0]
        self.assertEqual(delay, 3000)
        clear_status()
        self.assertEqual(panel._status_label.current, '')

    def test_later_validation_failure_keeps_memory_disk_and_draft(self):
        panel = panel_for(self.settings)
        panel._opacity_slider.current = 150
        draft = panel.draft()
        with patch.object(persistence.os, 'replace', wraps=self.real_replace) as replace:
            panel.save()
        self.assertEqual(panel.messages[0][0], 'critical')
        self.assertIn('display.overlay_opacity', panel.messages[0][2])
        self.assert_draft_retained(panel, draft)
        self.assert_unchanged()
        self.assertEqual(replace.call_count, 0)

    def test_partial_serializer_failure_keeps_memory_disk_and_draft(self):
        panel = panel_for(self.settings)
        draft = panel.draft()
        with patch('src.config.settings.yaml.dump', side_effect=partially_serialize):
            panel.save()
        self.assertEqual(panel.messages[0][0], 'critical')
        self.assertIn('serializer stopped', panel.messages[0][2])
        self.assert_draft_retained(panel, draft)
        self.assert_unchanged()

    def test_replace_refusal_then_retry_uses_latest_full_draft(self):
        panel = panel_for(self.settings)
        draft = panel.draft()
        with patch.object(persistence.os, 'replace', side_effect=PermissionError('replace refused')):
            panel.save()
        self.assertEqual(panel.messages[0][0], 'critical')
        self.assertIn('replace refused', panel.messages[0][2])
        self.assert_draft_retained(panel, draft)
        self.assert_unchanged()
        panel._name_edit.current = 'Latest character'
        panel._active_fps.current = 5.25
        panel._window_key.current = 'alt+shift+m'
        with patch.object(persistence.os, 'replace', wraps=self.real_replace) as replace:
            panel.save()
        self.assertEqual(replace.call_count, 1)
        latest = dict(PANEL_PATCH, **{'general.character_name': 'Latest character',
                                    'capture.active_fps': 5.25,
                                    'hotkeys.show_window': 'alt+shift+m'})
        self.assert_patch(latest)
        self.assertFalse(panel._unsaved_changes)
        self.assertFalse(panel._save_btn.current)
        self.assertEqual(panel._status_label.current, 'Settings saved!')

    def test_blank_hotkeys_keep_existing_fallbacks(self):
        panel = panel_for(self.settings)
        for name in ('_overlay_key', '_tracking_key', '_window_key'):
            getattr(panel, name).current = '  '
        panel.save()
        values = dict(PANEL_PATCH, **{'hotkeys.toggle_overlay': 'ctrl+shift+g',
                                     'hotkeys.toggle_tracking': 'ctrl+shift+t',
                                     'hotkeys.show_window': 'ctrl+shift+m'})
        self.assert_patch(values)
        self.assertEqual(panel.messages, [])

    def test_ui_validation_warnings_leave_draft_and_settings_unchanged(self):
        for changes, error in (
            ({'_name_edit': ' '}, 'Character name cannot be empty'),
            ({'_name_edit': 'x' * 51}, 'Character name is too long'),
            ({'_idle_fps': 5}, 'Idle FPS should be less than or equal'),
            ({'_overlay_key': ' alt+t '}, 'Hotkeys must be unique'),
        ):
            with self.subTest(error=error):
                panel = panel_for(self.settings)
                for name, value in changes.items():
                    getattr(panel, name).current = value
                draft = panel.draft()
                with patch.object(persistence.os, 'replace', wraps=self.real_replace) as replace:
                    panel.save()
                self.assertEqual(panel.messages[0][0], 'warning')
                self.assertIn(error, panel.messages[0][2])
                self.assert_draft_retained(panel, draft)
                self.assert_unchanged()
                self.assertEqual(replace.call_count, 0)


class TestSettingsBatch(SettingsCase):
    def test_persists_candidate_before_publishing_memory(self):
        def checked_replace(staged, destination):
            self.assertIs(self.settings._config, self.original_root)
            self.assertEqual(self.settings._config, self.original)
            self.assertEqual(self.path.read_bytes(), self.original_bytes)
            candidate = yaml.safe_load(Path(staged).read_text(encoding='utf-8'))
            self.assertEqual(candidate['general']['character_name'], 'Batch character')
            self.assertEqual(candidate['hotkeys']['show_window'], 'alt+m')
            return self.real_replace(staged, destination)

        with patch.object(persistence.os, 'replace', side_effect=checked_replace) as replace:
            self.settings.set_many(PANEL_PATCH)
        self.assertEqual(replace.call_count, 1)
        self.assertIsNot(self.settings._config, self.original_root)
        self.assert_patch(PANEL_PATCH)

    def test_invalid_later_key_does_not_write_or_mutate(self):
        with patch('src.config.settings.atomic_text_writer') as writer:
            with self.assertRaises(SettingsValidationError):
                self.settings.set_many({'general.character_name': 'Should not publish',
                                        'capture.idle_fps': 0.5, 'display.mode': 'invalid'})
        writer.assert_not_called()
        self.assert_unchanged()

    def test_all_fps_fields_reject_nonfinite_and_invalid_values(self):
        for key in ('capture.idle_fps', 'capture.active_fps', 'capture.business_fps'):
            for value in (float('nan'), float('inf'), float('-inf'), 10**400,
                          0.05, 61, False, '1', None, [], {}):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(SettingsValidationError):
                        self.settings.set_many({'general.character_name': 'Draft', key: value})
                    self.assert_unchanged()

    def test_partial_serialization_rolls_back_and_cleans_staging(self):
        with patch('src.config.settings.yaml.dump', side_effect=partially_serialize):
            with self.assertRaisesRegex(OSError, 'serializer stopped'):
                self.settings.set_many(PANEL_PATCH)
        self.assert_unchanged()

    def test_refused_replace_rolls_back_and_retry_commits_latest_values(self):
        with patch.object(persistence.os, 'replace', side_effect=PermissionError('replace refused')):
            with self.assertRaisesRegex(PermissionError, 'replace refused'):
                self.settings.set_many(PANEL_PATCH)
        self.assert_unchanged()
        latest = dict(PANEL_PATCH, **{'general.character_name': 'New draft'})
        self.settings.set_many(latest)
        self.assert_patch(latest)

    def test_ordered_overlapping_keys_and_new_nested_paths(self):
        incoming = {'labels': ['first']}
        self.settings.set_many({'custom.branch': incoming,
                                'custom.branch.labels': ['last'], 'custom.new.leaf': 7})
        self.assertEqual(self.settings.get('custom.branch'), {'labels': ['last']})
        self.assertEqual(self.settings.get('custom.new.leaf'), 7)
        self.assertEqual(incoming, {'labels': ['first']})
        self.assertEqual(yaml.safe_load(self.path.read_text()), self.settings._config)

    def test_incompatible_nested_path_keeps_memory_disk_and_inputs(self):
        incoming = {'nested': ['original']}
        with self.assertRaises(TypeError):
            self.settings.set_many({'custom.branch': incoming, 'general.character_name.child': 'bad'})
        self.assertEqual(incoming, {'nested': ['original']})
        self.assert_unchanged()

    def test_success_detaches_old_aliases_inputs_defaults_and_other_settings(self):
        old_section = self.settings.get_section('capture')
        old_nested = self.settings.get('custom.nested')
        incoming = {'list': ['value']}
        defaults = deepcopy(DEFAULT_CONFIG)
        second = Settings(self.path)
        second_original = deepcopy(second._config)
        self.settings.set_many({'capture.idle_fps': 1.5, 'custom.new': incoming})
        self.assertIsNot(old_section, self.settings.get_section('capture'))
        self.assertIsNot(old_nested, self.settings.get('custom.nested'))
        self.assertEqual(old_section, self.original['capture'])
        incoming['list'].append('external mutation')
        old_section['idle_fps'] = 40
        old_nested['labels'].append('old alias mutation')
        self.assertEqual(self.settings.get('custom.new'), {'list': ['value']})
        self.assertEqual(self.settings.get('capture.idle_fps'), 1.5)
        self.assertEqual(self.settings.get('custom.nested.labels'), ['keep me'])
        self.assertEqual(second._config, second_original)
        self.assertEqual(DEFAULT_CONFIG, defaults)
        self.assertEqual(yaml.safe_load(self.path.read_text()), self.settings._config)

    def test_empty_patch_is_a_noop(self):
        with patch('src.config.settings.atomic_text_writer') as writer:
            self.settings.set_many({})
        writer.assert_not_called()
        self.assert_unchanged()

    def test_standalone_set_update_and_reset_keep_existing_behavior(self):
        self.settings.set('capture.idle_fps', -2, save=False, validate=False)
        self.assertEqual(self.settings.get('capture.idle_fps'), -2)
        self.assertIs(self.settings._config, self.original_root)
        self.assertEqual(self.path.read_bytes(), self.original_bytes)
        self.settings.update_section('custom', {'more': 3}, save=False)
        self.assertEqual(self.settings.get('custom.more'), 3)
        self.settings.reset_section('capture', save=False)
        self.assertEqual(self.settings.get_section('capture'), DEFAULT_CONFIG['capture'])
        self.settings.set('capture.idle_fps', 0.75)
        self.assertEqual(yaml.safe_load(self.path.read_text()), self.settings._config)
        self.settings.reset_to_defaults()
        self.assertEqual(self.settings._config, DEFAULT_CONFIG)
        self.assertEqual(yaml.safe_load(self.path.read_text()), DEFAULT_CONFIG)


if __name__ == '__main__':
    unittest.main()
