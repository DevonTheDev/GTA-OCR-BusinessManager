"""Review one detached live reading before copying it into a manual draft."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from ...database.business_checkins import business_label


_SOURCE_LABELS = {
    'manual_entry': 'Manual live entry',
    'ocr_text': 'OCR text match',
    'selected_target': 'OCR with selected business target',
}


def _observed_time(value):
    if value is None:
        return 'Unknown'
    text = value.isoformat(sep=' ', timespec='seconds')
    return f'{text} (local time)' if value.tzinfo is None else text


class BusinessCheckInLivePreview(QDialog):
    """A modal decision about already captured values, with no live reads."""

    def __init__(self, snapshot, character_id, character_name, parent=None):
        super().__init__(parent)
        self._snapshot = snapshot
        self.setWindowTitle('Preview live values for manual check-in')
        self.setModal(True)
        self.resize(620, 540)
        layout = QVBoxLayout(self)
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        self._context_label = self._label(
            f'Saved character: {character_name or "Saved character"} (#{character_id})\n'
            f'Business: {business_label(snapshot.business_id)} · {snapshot.business_id}'
        )
        body.addWidget(self._context_label)
        self._help_label = self._label(
            'Live readings are not character-tagged. Verify that these values belong '
            'to the saved character and business shown above. This preview stays fixed '
            'even if the live reading changes.\n\n'
            'Use these values replaces all three draft measurements, clearing unknown '
            'or not applicable fields. Your personal note stays untouched. Nothing is '
            'saved until you press Save in the check-in editor; Save uses its own time. '
            'The source and preview times below are not stored in the check-in.'
        )
        body.addWidget(self._help_label)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self._stock_label = self._label(
            'Unknown' if snapshot.stock_percent is None else f'{snapshot.stock_percent}%'
        )
        self._supply_label = self._label(
            'N/A' if not snapshot.uses_supplies else
            'Unknown' if snapshot.supply_percent is None else f'{snapshot.supply_percent}%'
        )
        self._value_label = self._label(
            'Unknown' if snapshot.stock_value is None else f'${snapshot.stock_value}'
        )
        self._source_label = self._label(_SOURCE_LABELS.get(snapshot.identity_source, 'Source not recorded'))
        self._updated_label = self._label(_observed_time(snapshot.updated_at))
        self._captured_label = self._label(
            f'{snapshot.captured_at.isoformat(sep=" ", timespec="microseconds")} UTC'
        )
        for title, label in (
            ('Observed stock', self._stock_label), ('Observed supplies', self._supply_label),
            ('Observed stock value', self._value_label), ('Reading source', self._source_label),
            ('Live reading updated', self._updated_label), ('Preview captured', self._captured_label),
        ):
            form.addRow(title, label)
        body.addLayout(form)
        body.addStretch()
        self._scroll_area.setWidget(content)
        layout.addWidget(self._scroll_area, 1)
        buttons = QDialogButtonBox()
        self._use_button = buttons.addButton('Use these values', QDialogButtonBox.ButtonRole.AcceptRole)
        self._cancel_button = buttons.addButton('Cancel', QDialogButtonBox.ButtonRole.RejectRole)
        # Opening a preview must not turn an incidental Enter into replacement.
        self._cancel_button.setDefault(True)
        self._use_button.setAutoDefault(False)
        self._use_button.clicked.connect(self.accept)
        self._cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _label(text):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        label.setSizePolicy(policy)
        return label
