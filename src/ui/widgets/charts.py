"""Charts and graphs for session visualization."""

from typing import TYPE_CHECKING, List, Dict, Optional, Tuple
from datetime import datetime

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PyQt6.QtCore import QTimer

try:
    import pyqtgraph as pg
    PYQTGRAPH_AVAILABLE = True
except ImportError:
    PYQTGRAPH_AVAILABLE = False

from ...utils.helpers import format_money_short

if TYPE_CHECKING:
    from ...app import GTABusinessManager


class EarningsChart(QWidget):
    """Chart showing earnings over time during the session."""

    def __init__(self, app: Optional["GTABusinessManager"] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._app = app
        self._earnings_history: List[Tuple[float, int]] = []  # [(elapsed_minutes, cumulative_earnings), ...]
        self._last_earnings: int = 0
        self._session_start: datetime = datetime.now()
        self._setup_ui()
        self._setup_timer()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if not PYQTGRAPH_AVAILABLE:
            label = QLabel("Charts unavailable - install pyqtgraph")
            label.setStyleSheet("color: #666; font-size: 12px;")
            layout.addWidget(label)
            return

        # Configure pyqtgraph for dark theme
        pg.setConfigOptions(antialias=True, background="#1a1a2e", foreground="#ffffff")

        # Create plot widget
        self._plot = pg.PlotWidget()
        self._plot.setTitle("Session Earnings", color="#ffffff", size="12pt")
        self._plot.setLabel("left", "Earnings ($)")
        self._plot.setLabel("bottom", "Time (minutes)")
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._plot.setMinimumHeight(200)

        # Style the axes
        self._plot.getAxis("left").setTextPen("#aaaaaa")
        self._plot.getAxis("bottom").setTextPen("#aaaaaa")

        # Create the line plot
        self._curve = self._plot.plot(
            [], [],
            pen=pg.mkPen(color="#4CAF50", width=2),
            symbol="o",
            symbolSize=6,
            symbolBrush="#4CAF50"
        )

        layout.addWidget(self._plot)

    def _setup_timer(self) -> None:
        """Setup update timer."""
        self._timer = QTimer()
        self._timer.timeout.connect(self._update_chart)
        self._timer.start(5000)  # Update every 5 seconds

    def _update_chart(self) -> None:
        """Update chart with latest data."""
        if not PYQTGRAPH_AVAILABLE or not self._app:
            return

        current_earnings = self._app.session_earnings

        # Record data point if earnings changed
        if current_earnings != self._last_earnings:
            elapsed = (datetime.now() - self._session_start).total_seconds() / 60
            self._earnings_history.append((elapsed, current_earnings))
            self._last_earnings = current_earnings

        # Add current point even if no change (for continuous line)
        if self._earnings_history:
            elapsed = (datetime.now() - self._session_start).total_seconds() / 60

            # Prepare data for plotting
            times = [point[0] for point in self._earnings_history]
            earnings = [point[1] for point in self._earnings_history]

            # Add current point
            times.append(elapsed)
            earnings.append(current_earnings)

            self._curve.setData(times, earnings)

    def reset(self) -> None:
        """Reset chart for new session."""
        self._earnings_history = []
        self._last_earnings = 0
        self._session_start = datetime.now()
        if PYQTGRAPH_AVAILABLE:
            self._curve.setData([], [])


class ActivityBreakdownChart(QWidget):
    """Bar chart showing earnings breakdown by activity type."""

    def __init__(self, app: Optional["GTABusinessManager"] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._app = app
        self._setup_ui()
        self._setup_timer()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if not PYQTGRAPH_AVAILABLE:
            label = QLabel("Charts unavailable - install pyqtgraph")
            label.setStyleSheet("color: #666; font-size: 12px;")
            layout.addWidget(label)
            return

        # Configure for dark theme
        pg.setConfigOptions(antialias=True, background="#1a1a2e", foreground="#ffffff")

        # Create plot widget
        self._plot = pg.PlotWidget()
        self._plot.setTitle("Earnings by Source", color="#ffffff", size="12pt")
        self._plot.setLabel("left", "Earnings ($)")
        self._plot.showGrid(x=False, y=True, alpha=0.3)
        self._plot.setMinimumHeight(180)

        # Style axes
        self._plot.getAxis("left").setTextPen("#aaaaaa")
        self._plot.getAxis("bottom").setTextPen("#aaaaaa")

        # Bar graph item
        self._bar_item = pg.BarGraphItem(x=[], height=[], width=0.6, brush="#4CAF50")
        self._plot.addItem(self._bar_item)

        layout.addWidget(self._plot)

    def _setup_timer(self) -> None:
        """Setup update timer."""
        self._timer = QTimer()
        self._timer.timeout.connect(self._update_chart)
        self._timer.start(10000)  # Update every 10 seconds

    def _update_chart(self) -> None:
        """Update chart with latest breakdown data."""
        if not PYQTGRAPH_AVAILABLE or not self._app:
            return

        breakdown = self._app.earnings_breakdown
        if not breakdown:
            return

        # Prepare data
        categories: List[str] = []
        values: List[int] = []
        colors: List[str] = []

        category_config: List[Tuple[str, int, str]] = [
            ("Missions", breakdown.from_missions, "#2196F3"),
            ("Sells", breakdown.from_sells, "#4CAF50"),
            ("VIP Work", breakdown.from_vip_work, "#FF9800"),
            ("Heists", breakdown.from_heists, "#9C27B0"),
            ("Other", breakdown.from_other, "#607D8B"),
        ]

        for name, value, color in category_config:
            if value > 0:
                categories.append(name)
                values.append(value)
                colors.append(color)

        if not values:
            return

        # Update bar chart
        x = list(range(len(values)))

        # Create colored bars
        self._plot.clear()
        for i, (val, color) in enumerate(zip(values, colors)):
            bar = pg.BarGraphItem(x=[i], height=[val], width=0.6, brush=color)
            self._plot.addItem(bar)

        # Update x-axis labels
        axis = self._plot.getAxis("bottom")
        axis.setTicks([[(i, cat) for i, cat in enumerate(categories)]])

    def reset(self) -> None:
        """Reset chart."""
        if PYQTGRAPH_AVAILABLE:
            self._plot.clear()


class SessionCharts(QWidget):
    """Combined charts panel for session statistics."""

    def __init__(self, app: Optional["GTABusinessManager"] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._app = app
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header
        header = QLabel("Session Analytics")
        header.setStyleSheet("color: white; font-size: 16px; font-weight: bold;")
        layout.addWidget(header)

        if not PYQTGRAPH_AVAILABLE:
            msg = QLabel(
                "Charts require pyqtgraph.\n"
                "Install with: pip install pyqtgraph"
            )
            msg.setStyleSheet("color: #666; font-size: 12px;")
            layout.addWidget(msg)
            return

        # Charts in horizontal layout
        charts_layout = QHBoxLayout()
        charts_layout.setSpacing(16)

        # Earnings over time chart
        earnings_frame = QFrame()
        earnings_frame.setStyleSheet("""
            QFrame {
                background-color: #16213e;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        earnings_layout = QVBoxLayout(earnings_frame)
        self._earnings_chart = EarningsChart(app=self._app)
        earnings_layout.addWidget(self._earnings_chart)
        charts_layout.addWidget(earnings_frame)

        # Activity breakdown chart
        breakdown_frame = QFrame()
        breakdown_frame.setStyleSheet("""
            QFrame {
                background-color: #16213e;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        breakdown_layout = QVBoxLayout(breakdown_frame)
        self._breakdown_chart = ActivityBreakdownChart(app=self._app)
        breakdown_layout.addWidget(self._breakdown_chart)
        charts_layout.addWidget(breakdown_frame)

        layout.addLayout(charts_layout)

    def reset(self) -> None:
        """Reset all charts for new session."""
        if PYQTGRAPH_AVAILABLE:
            if hasattr(self, "_earnings_chart"):
                self._earnings_chart.reset()
            if hasattr(self, "_breakdown_chart"):
                self._breakdown_chart.reset()
