# GTA Online Business Manager

A smart companion app that runs alongside GTA Online, tracking your money, activities, and businesses in real-time using screen capture technology. Get personalized recommendations to maximize your earnings.

---

## Quick Start (For Everyone)

### What You Need
- **Windows 10 or 11** (required - uses Windows OCR)
- **Python 3.11 or newer** - [Download here](https://www.python.org/downloads/)
- **GTA V with GTA Online**

### Easy Installation

1. **Download Python** from [python.org](https://www.python.org/downloads/)
   - During installation, **check the box that says "Add Python to PATH"** - this is important!

2. **Download this app** - Click the green "Code" button above, then "Download ZIP", and extract it somewhere easy to find (like your Desktop)

3. **Install the app** - Double-click `install.bat` in the extracted folder
   - A black window will appear and install everything needed
   - Wait until it says "Installation complete!"

4. **Run the app** - Double-click `run.bat`
   - The app will start and show a small icon in your system tray (bottom-right of screen, near the clock)

### How to Use

1. **Start GTA Online** and load into a session
2. **Start the Business Manager** by double-clicking `run.bat`
3. A small **overlay** will appear in the corner of your screen showing:
   - Your current money
   - Session earnings (how much you've made since starting)
   - What activity you're doing
   - Recommendations for what to do next

**Tip:** You can drag the overlay to move it, or right-click the tray icon for more options.

### Controls

| Action | How |
|--------|-----|
| Show/Hide Overlay | `Ctrl + Shift + G` |
| Pause/Resume Tracking | `Ctrl + Shift + T` |
| Open Dashboard | `Ctrl + Shift + M` or double-click tray icon |
| Access Menu | Right-click the tray icon |
| Move Overlay | Click and drag it |

### Troubleshooting

**"Python is not recognized"**
- You need to reinstall Python and check "Add Python to PATH" during installation

**App doesn't detect my money**
- Make sure GTA is in **Borderless Windowed** or **Windowed** mode (not exclusive fullscreen)
- The money display must be visible on screen

**Overlay doesn't show**
- Press `Ctrl + Shift + G` to toggle it
- Or right-click the tray icon and select "Show Overlay"

**Nothing happens when I double-click run.bat**
- Right-click `run.bat` and select "Run as administrator"

---

## Features

### Real-Time Tracking
- **Money Monitoring** - Tracks your bank balance changes
- **Session Stats** - See how much you've earned this session
- **Earnings Rate** - Calculates your $/hour based on actual gameplay

### Activity Detection
- Automatically detects when you're doing:
  - Contact Missions
  - CEO/VIP Work
  - MC Contracts
  - Sell Missions
  - Heists
  - And more...

### Business Management
- Track stock and supply levels for all businesses:
  - MC Businesses (Cocaine, Meth, Cash, Weed, Documents)
  - Bunker
  - Nightclub
  - Agency
  - Acid Lab
  - And more...

### Smart Recommendations
- Get suggestions like:
  - "Your bunker is ready to sell"
  - "Cocaine supplies running low"
  - "Nightclub safe approaching max"

### Display Options
- **Overlay Mode** - Small transparent window over your game
- **Dashboard Mode** - Full window with detailed stats
- **Tray Only** - Minimized to system tray

---

## For Technical Users

### Project Structure

```
gta-business-manager/
├── src/                      # Source code
│   ├── main.py               # Entry point
│   ├── app.py                # Main orchestrator
│   ├── hotkeys.py            # Global hotkey handling
│   ├── capture/              # Screen capture (mss library)
│   ├── detection/            # OCR & state detection
│   ├── game/                 # GTA-specific definitions
│   ├── tracking/             # Session & activity tracking
│   ├── optimization/         # Recommendation engine
│   ├── database/             # SQLite + SQLAlchemy
│   ├── ui/                   # PyQt6 interface
│   ├── config/               # Settings management
│   └── utils/                # Logging, helpers
├── assets/                   # Templates & sounds
├── tests/                    # Test suite
├── build.py                  # PyInstaller build script
├── requirements.txt          # Dependencies
└── pyproject.toml            # Project metadata
```

### Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Screen Capture | mss |
| OCR | winocr (Windows OCR API) |
| Image Processing | OpenCV, Pillow |
| Database | SQLite + SQLAlchemy |
| GUI | PyQt6 |
| Config | YAML |
| Packaging | PyInstaller |

### Manual Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/gta-business-manager.git
cd gta-business-manager

# Create virtual environment (recommended)
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the application
python -m src.main
```

### Command Line Options

```bash
python -m src.main              # GUI mode with overlay (default)
python -m src.main --no-overlay # GUI mode without overlay
python -m src.main --console    # Console-only mode (no GUI)
python -m src.main --debug      # Enable debug logging
```

### Building an Executable

```bash
# Build single .exe file
python build.py

# Build as directory (faster startup)
python build.py onedir
```

Output will be in `dist/GTABusinessManager.exe`

### Configuration

Settings are stored in:
- Windows: `%LOCALAPPDATA%\GTABusinessManager\config.yaml`

Example configuration:
```yaml
general:
  character_name: "Default"
  minimize_to_tray: true

display:
  mode: "overlay"  # overlay | window | both
  overlay_position: "top-right"
  overlay_opacity: 0.85

capture:
  idle_fps: 0.5
  active_fps: 2.0
  monitor_index: 0

hotkeys:
  toggle_overlay: "ctrl+shift+g"
  toggle_tracking: "ctrl+shift+t"
  show_window: "ctrl+shift+m"

notifications:
  audio_enabled: false
```

### Database

SQLite database stored at `%LOCALAPPDATA%\GTABusinessManager\gta_manager.db`

**Tables:**
- `characters` - Player characters
- `sessions` - Play sessions with earnings
- `activities` - Completed activities with earnings/duration
- `business_snapshots` - Business stock/supply history
- `earnings` - Individual money transactions

### Architecture

**Capture Pipeline:**
```
Screen Capture (mss) → Region Extraction → OCR (winocr) → Parsing → State Detection
```

**Adaptive Capture Rates:**
- Idle: 0.5 FPS (every 2 seconds)
- Active: 2.0 FPS (every 500ms)

**State Machine:**
```
IDLE → MISSION_ACTIVE → MISSION_COMPLETE
         ↓
      SELLING → MISSION_COMPLETE
```

### Screen Regions

All regions defined as relative coordinates (0.0-1.0) for multi-resolution support:

| Region | Purpose | Default Position |
|--------|---------|------------------|
| money_display | Bank balance | Top-right |
| mission_text | Mission objectives | Top-center |
| timer_display | Countdown timers | Bottom-right |
| center_screen | Prompts/notifications | Center |

### Performance

| Metric | Target |
|--------|--------|
| CPU (idle) | < 1% |
| CPU (active) | < 3% |
| Memory | < 100MB |
| OCR latency | < 50ms |

### Adding Custom Templates

Place template images in `assets/templates/`:
- `icons/` - Game icons for matching
- `ui_elements/` - UI component templates

Templates should be PNG files captured at 1080p for best results.

### Testing

```bash
# Run test suite
pytest tests/

# Test capture pipeline manually
python test_capture.py
```

### API/Extending

```python
from src.app import GTABusinessManager
from src.config.settings import get_settings

# Create app instance
settings = get_settings()
app = GTABusinessManager(settings)

# Register callbacks
app.on_capture(lambda result: print(f"Money: {result.money}"))
app.on_money_change(lambda reading, change: print(f"Earned: ${change}"))

# Start tracking
app.start()
```

---

## FAQ

**Is this a mod or hack?**
No. This app only reads your screen - it never touches game files or memory. It's like a human watching your screen and taking notes.

**Will I get banned?**
This app uses only screen capture and OCR (reading text from images). It does not modify game files, inject code, or read game memory. However, always use third-party tools at your own discretion.

**Does it work with all resolutions?**
Yes, it's designed to work with 1080p, 1440p, and 4K. All screen positions are calculated as percentages.

**Does it work in fullscreen?**
It works best in **Borderless Windowed** mode. Exclusive fullscreen may not work properly with the overlay.

**Can I use it on multiple characters?**
Yes! The app supports multiple characters and tracks them separately.

**Where is my data stored?**
All data is stored locally on your computer in `%LOCALAPPDATA%\GTABusinessManager\`

---

## License

MIT License - See [LICENSE](LICENSE) file for details.

---

## Contributing

Contributions welcome! Please feel free to submit issues or pull requests.

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

---

## Acknowledgments

- Uses the Windows OCR API via [winocr](https://github.com/poa00/winocr)
- Screen capture powered by [mss](https://github.com/BoboTiG/python-mss)
- UI built with [PyQt6](https://www.riverbankcomputing.com/software/pyqt/)
