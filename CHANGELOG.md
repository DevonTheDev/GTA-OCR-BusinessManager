# Changelog

All notable changes to GTA Business Manager will be documented in this file.

## [0.1.0] - Initial Release

### Features

#### Screen Capture & Detection
- Real-time screen capture using mss library
- Windows OCR integration via winocr for text recognition
- Adaptive capture rates (0.5 FPS idle, 2.0 FPS active, 4.0 FPS business UI)
- Multi-resolution support (1080p, 1440p, 4K)
- Region-based capture for efficiency

#### Game State Detection
- Automatic detection of game states:
  - Idle/Freeroam
  - Mission Active
  - Mission Complete/Failed
  - Sell Missions
  - Heist Prep/Finale
  - Business Computer
  - Loading/Cutscenes
- Keyword-based text recognition for:
  - VIP Work (Headhunter, Sightseer, etc.)
  - MC Contracts
  - Agency Security Contracts
  - Payphone Hits
  - Auto Shop Contracts
  - Nightclub Management

#### Tracking
- Real-time money tracking from HUD
- Session earnings calculation
- Activity duration tracking
- Earnings per hour calculation
- Mission completion detection

#### Business Tracking
- Support for all major businesses:
  - MC Businesses (Cocaine, Meth, Cash, Weed, Documents)
  - Bunker
  - Nightclub
  - Agency
  - Acid Lab
  - Vehicle Warehouse
  - Special Cargo
- Stock and supply level tracking
- Business state persistence

#### User Interface
- System tray integration with quick access menu
- Transparent overlay window (draggable, configurable)
- Full dashboard window with tabs:
  - Dashboard overview
  - Session statistics
  - Business status
  - Activity history
  - Recommendations
  - Settings
- GTA-inspired dark theme

#### Recommendations
- Smart recommendations for next activities
- Business sell notifications
- Supply warnings
- Safe collection reminders

#### Configuration
- YAML-based configuration
- Hotkey customization:
  - Toggle overlay (Ctrl+Shift+G)
  - Toggle tracking (Ctrl+Shift+T)
  - Show window (Ctrl+Shift+M)
- Display mode options (overlay/window/both)
- Capture rate customization
- Audio notification settings

#### Database
- SQLite database for persistent storage
- Character support for multiple accounts
- Session history
- Activity logging
- Business snapshots
- Earnings records

### Technical
- Python 3.11+ support
- PyInstaller packaging for .exe distribution
- Comprehensive logging system
- Performance monitoring
- Thread-safe UI updates

---

## Planned Features

### Next Release
- [ ] Template matching for more accurate UI detection
- [ ] Audio notifications for important events
- [ ] Historical charts and graphs
- [ ] Export session data to CSV
- [ ] Auto-start with Windows option

### Future
- [ ] Multi-monitor support
- [ ] Custom overlay themes
- [ ] Activity predictions based on history
- [ ] Discord integration
- [ ] Backup/restore settings
