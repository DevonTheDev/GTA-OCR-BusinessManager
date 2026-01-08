"""Dark theme styling for GTA Business Manager."""


class DarkTheme:
    """GTA-inspired dark theme."""

    # Color palette
    COLORS = {
        "background": "#1a1a2e",
        "surface": "#16213e",
        "primary": "#e94560",
        "secondary": "#0f3460",
        "accent": "#ffd700",
        "text": "#ffffff",
        "text_secondary": "#a0a0a0",
        "success": "#4caf50",
        "warning": "#ff9800",
        "error": "#f44336",
        "money_green": "#4caf50",
    }

    @classmethod
    def get_stylesheet(cls) -> str:
        """Get the complete stylesheet for the application."""
        return f"""
            QMainWindow {{
                background-color: {cls.COLORS['background']};
            }}

            QWidget {{
                background-color: {cls.COLORS['background']};
                color: {cls.COLORS['text']};
            }}

            QLabel {{
                color: {cls.COLORS['text']};
            }}

            QPushButton {{
                background-color: {cls.COLORS['secondary']};
                color: {cls.COLORS['text']};
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
            }}

            QPushButton:hover {{
                background-color: {cls.COLORS['primary']};
            }}

            QPushButton:pressed {{
                background-color: #c73e54;
            }}

            QTabWidget::pane {{
                background-color: {cls.COLORS['surface']};
                border: 1px solid {cls.COLORS['secondary']};
                border-radius: 4px;
            }}

            QTabBar::tab {{
                background-color: {cls.COLORS['secondary']};
                color: {cls.COLORS['text']};
                padding: 8px 16px;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }}

            QTabBar::tab:selected {{
                background-color: {cls.COLORS['primary']};
            }}

            QTableWidget {{
                background-color: {cls.COLORS['surface']};
                alternate-background-color: {cls.COLORS['background']};
                gridline-color: {cls.COLORS['secondary']};
            }}

            QTableWidget::item {{
                color: {cls.COLORS['text']};
            }}

            QHeaderView::section {{
                background-color: {cls.COLORS['secondary']};
                color: {cls.COLORS['text']};
                padding: 8px;
                border: none;
            }}

            QScrollBar:vertical {{
                background-color: {cls.COLORS['background']};
                width: 12px;
                border-radius: 6px;
            }}

            QScrollBar::handle:vertical {{
                background-color: {cls.COLORS['secondary']};
                border-radius: 6px;
                min-height: 20px;
            }}

            QScrollBar::handle:vertical:hover {{
                background-color: {cls.COLORS['primary']};
            }}

            QLineEdit {{
                background-color: {cls.COLORS['surface']};
                color: {cls.COLORS['text']};
                border: 1px solid {cls.COLORS['secondary']};
                border-radius: 4px;
                padding: 6px;
            }}

            QLineEdit:focus {{
                border-color: {cls.COLORS['primary']};
            }}

            QComboBox {{
                background-color: {cls.COLORS['surface']};
                color: {cls.COLORS['text']};
                border: 1px solid {cls.COLORS['secondary']};
                border-radius: 4px;
                padding: 6px;
            }}

            QComboBox::drop-down {{
                border: none;
            }}

            QProgressBar {{
                background-color: {cls.COLORS['surface']};
                border: none;
                border-radius: 4px;
                text-align: center;
            }}

            QProgressBar::chunk {{
                background-color: {cls.COLORS['primary']};
                border-radius: 4px;
            }}

            QStatusBar {{
                background-color: {cls.COLORS['surface']};
                color: {cls.COLORS['text_secondary']};
            }}

            QMenuBar {{
                background-color: {cls.COLORS['surface']};
                color: {cls.COLORS['text']};
            }}

            QMenuBar::item:selected {{
                background-color: {cls.COLORS['primary']};
            }}

            QMenu {{
                background-color: {cls.COLORS['surface']};
                color: {cls.COLORS['text']};
                border: 1px solid {cls.COLORS['secondary']};
            }}

            QMenu::item:selected {{
                background-color: {cls.COLORS['primary']};
            }}
        """

    @classmethod
    def money_color(cls, amount: int, threshold: int = 0) -> str:
        """Get color for money display.

        Args:
            amount: Money amount
            threshold: Threshold for positive/negative

        Returns:
            Color string
        """
        if amount > threshold:
            return cls.COLORS["success"]
        elif amount < threshold:
            return cls.COLORS["error"]
        return cls.COLORS["text"]
