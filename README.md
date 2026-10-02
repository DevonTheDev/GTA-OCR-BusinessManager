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

### Running Tests

Run the automated, hardware-independent unit suite with `python -m pytest`.
For a fresh, hardware-independent test environment, install
`python -m pip install -r requirements-test.txt`. This includes headless OpenCV
and imports the real app for accounting tests without starting screen capture.

On Windows, separately run `python test_capture.py` with the runtime dependencies
installed to diagnose screen capture and OCR. This interactive script requires a
display and Windows OCR; it is not collected by the automated unit suite.

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

Capture pacing uses elapsed monotonic time, so changing the system clock cannot
create an hour-long wait or bypass the rate limit. Failed screen grabs are paced
as well; a disconnected or unavailable capture backend does not trigger a tight
retry loop. Batched HUD grabs still wait only once between batches. These are
scheduling guarantees, not measured native Windows FPS benchmarks.

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

### Capture lifecycle checks

Each Start begins fresh session money/mission state. Previous business observations
and completed activity history remain available. A Stop request waits up to five
seconds for an in-flight capture; if it is still busy, the app remains `STOPPING`
and refuses another Start. The worker closes its resources and database session
when that capture returns. Stopping from a capture callback is also supported.

Shutdown is best-effort if a component's cleanup raises: the failure is logged and
remaining resources are still cleaned up. A database-close failure can leave an
old repository handle needing later recovery. A permanently blocked OCR call or
forced process exit cannot guarantee final session persistence.

If SQLite cannot initialize, repository methods retain their documented empty or
failure results instead of crashing with an uninitialized session factory. They
retry initialization on the next operation, allowing recovery after the database
location is repaired. Tests use temporary paths; no live data is modified.

Offline regression tests use real threads and temporary SQLite with synthetic
capture. Windows smoke checks remain important: stop during OCR, wait for
`STOPPED`, restart with a different displayed balance, and confirm that the first
reading is an opening balance rather than income. These tests do not exercise
real Windows OCR, Qt or native capture-resource cleanup.

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

### Optional performance metrics

Install `pip install -e ".[metrics]"` (or `pip install psutil`) to enable process
CPU/RAM sampling. The process sampler is reused across capture and UI reads,
with one synchronized sample at most every half second. Its first CPU sample is
zero while the baseline is established; subsequent samples can exceed 100% when
multiple CPU cores are active, following psutil's process-percent convention.

If psutil is missing or a particular OS metric is denied, that optional metric
is reported as zero while capture timing remains available. Transient failures
are retried on later samples. FPS uses monotonic elapsed time, independently of
system clock changes. Resetting the monitor clears its timing and CPU baseline.
Synthetic tests check these semantics; they are not Windows performance benchmarks.

### Safer local state saves

Settings, cooldowns, goals, session history, nightclub state, passive income and
weekly bonuses stage their YAML/JSON in a unique file beside the destination.
The writer closes before replacing the saved file, so interrupted serialization,
flush/close errors and ordinary replacement failures preserve the last good save.
Existing symlink destinations still update their targets. Serialization formats
and each store's existing error reporting are unchanged.

This is per-file replacement, not a concurrent-update lock, multi-file transaction
or power-loss durability guarantee. In-memory changes are not rolled back after a
failed save; a later successful save can persist them. Temporary files are cleaned
up on ordinary failures; an abrupt process exit or denied cleanup may leave a
`.tmp` file. Cleanup failures are logged without hiding the original save error.
Local Python tests use disposable real files and injected failures. Native Windows
filesystem behavior remains a separate runtime check; no existing saves are
migrated or rewritten until the app performs its normal save operation.

### Analytics cache refresh

Analytics getters refresh at most once per one-second elapsed-time interval,
including when a result is already cached, so elapsed-session rates do not stay
frozen until another mission completes. Concurrent refresh calls share one
calculation. Both earnings and efficiency calculations must succeed before their
new results are published; empty histories clear stale results, while failures
retain the previous snapshot and still respect the retry interval.

Refresh scheduling uses a monotonic clock and starts its budget after each attempt
finishes. Start/reset invalidates the cache and its budget; an explicit forced
refresh can bypass the interval. This changes cache behavior, not the selected
activity history or analytics formulas, and does not make every tracker operation
thread-safe. Tests use real calculations with controlled clocks and threads;
there is no native UI or FPS benchmark claim.

### Opening-balance recovery at session end

The first observed balance is retained separately for the database session, so
resetting the on-screen statistics cannot replace it. Normal finalization saves
that original balance, the ending balance and their net difference in one SQLite
transaction. This recovers a failed initial opening-balance write when storage is
available again at session end. UI earnings remain gross positive money changes;
database session earnings remain the net balance difference.

Finalization only updates an open row, so repeated or competing finalizers cannot
rewrite closed history. Success is logged only after commit; failures are reported
and roll back the transaction. This is not a background retry journal: active
exports may still show the old baseline after an initial failure, and a persistent
storage failure or abrupt process exit can leave the session unfinished. Local
tests exercise real SQLite, injected commit failures and concurrent finalizers;
native Windows OCR and crash recovery remain separate validation work.

### Completed session statistics

Stopping a session records its end time. Its displayed duration and session
average earnings/hour then stay fixed while the stopped view or analytics cache
refreshes. Tracker update methods ignore late balance, activity and time updates
after completion. Repeated stop calls preserve the original end time; starting
another session creates fresh, live statistics without reopening the prior one.

This applies to `SessionStats` and `SessionTracker`, not the separate rolling
rate windows or cross-session activity-history selection. Active-session wall
clock behavior is unchanged. The returned dataclass remains mutable by callers;
this is not an immutable snapshot or a new thread-safety guarantee. Local tests
use controlled clocks and the application's stop/analytics path without native
capture, OCR or Qt UI execution.

### OCR balance validation baseline

Money plausibility checks compare each candidate with the last balance accepted
by validation. Parsing alone, rejected candidates and later mutation of a returned
reading do not replace that numeric baseline. This prevents the parse→validate
capture flow from accepting a large OCR spike simply by comparing it with itself.
The existing parsed-reading accessor still returns the latest parsed candidate.

The existing minimum/maximum limits and 100× jump threshold are unchanged.
Repeated rejected jumps do not automatically establish a new baseline; a fresh
capture run creates a new parser. A genuine unusually large balance change may
therefore need a restart or future threshold-policy adjustment. Tests use synthetic
OCR text through the actual capture/accounting path and disposable SQLite, without
Windows OCR, screenshots, live gameplay or an accuracy benchmark.

### Activity export periods and row counts

Activity-history and earnings-breakdown exports now use the activity's completion
time within the requested UTC lookback window, rather than ignoring the period or
using the parent session's start date. Records without a completion time use their
start time; undated and future-dated records are excluded. Both window boundaries
are inclusive, `days` must be a nonnegative integer, and zero means the single
query instant. Existing earnings/count/average formulas are unchanged.

The history export queries all matching activities for the selected character,
without the former 1,000-session cap or one activity query per session. Rows are
newest first with an ID tie-breaker. A session-existence check preserves the
existing no-session error and header-only empty-result behavior. Breakdown row
counts now report data rows actually written, excluding the header.

Invalid periods are rejected before an existing export is opened. This does not
provide a multi-query database snapshot or alter session-level totals. Per-file
write recovery is described below. Local tests use real disposable SQLite and CSV files,
including older sessions with recent activity, exact boundaries, other characters,
more than 1,000 sessions and query-count checks; no gameplay performance claim.

### Recoverable export writes

CSV and JSON exports stage each output file beside its destination and replace it
only after serialization and handle closure succeed. A partial write, close or
replacement failure leaves the previous version of that file intact; a failed
first write leaves no partial final file. Export methods keep their existing
failure result and normal retry behavior. UTF-8, CSV quoting/newlines, JSON
formatting, filenames and row counts are unchanged. Existing symlink destinations
continue to update their targets.

This is per-file replacement: a session's info, activities and earnings files are
published separately. If a later file fails, earlier files may already contain the
new export. It is not an all-files/database transaction, concurrent-update lock or
power-loss durability guarantee. Temporary files are removed on ordinary failure;
abrupt exit or denied cleanup may leave one behind. Tests exercise real disposable
SQLite/files, injected I/O errors and quoted multiline CSV content. Native Windows
filesystem behavior and spreadsheet formula-text handling remain separate work.

### Completed goal history and notifications

Once a goal reaches its target, normal progress updates retain that first completed
value and timestamp. Later session resets or rising totals cannot reopen the same
goal, notify completion again or change its saved history. Setting another goal
still creates a fresh goal, and incomplete goals keep their existing update behavior.
Legacy completed goals without a timestamp stay complete without inventing one.

Completion records the finished goal before notifying listeners. Every listener
for that event receives the same goal, even if an earlier listener clears it,
replaces it or completes another goal. Listener registrations made during a
notification apply to later completion events. Individual callback failures remain
logged without preventing other listeners or the normal save.

Local tests cover all three goal types, saved JSON, resets and reentrant listeners.
This protects normal update/notification methods; returned dataclasses remain
mutable, and this is not a general thread-safety guarantee or a migration of
previously duplicated history. Native Qt notifications remain unverified.

### Mission result bookkeeping

Mission completion treats a zero balance as a known value, so an activity that
starts at $0 can record its positive balance change. Missing balances still yield
zero activity earnings, and spending still cannot become a negative payout. This
retains the existing balance-difference calculation and separate session/earnings
ledgers; it does not infer a reward directly from OCR result-screen text.

Both successful and failed activities retain their tracked type and name when
saved to SQLite. Sell missions therefore keep their label, and successful sells
increment the existing session sell counter. A missing tracked activity keeps the
generic fallback. Repeated result screens remain ignored after the mission resets.
Existing history is not migrated or relabeled.

Local tests use synthetic detected states through the actual trackers and SQLite,
covering zero/missing/spent balances, failed activity types, sell counts and the
money-plus-mission path without duplicate session income. Native OCR accuracy,
gameplay, UI rendering and mission classification heuristics remain unverified.
