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

### Temporarily Hide a Recommendation

Open **Recommendations** and choose **Snooze 10 min** on a suggestion. It is
hidden from this panel, the dashboard and the overlay while other suggestions
move up. Use **Restore all** to bring back every snoozed suggestion immediately.
The status distinguishes suggestions currently hidden from snoozes whose
suggestion is temporarily unavailable. Button actions refresh this panel
immediately; the dashboard, overlay and automatic expiry follow their normal
display refresh timers.

A snooze follows the same action even when its wording, value or urgency
changes. Selling and resupplying a business remain separate choices, as do
Headhunter and Sightseer. Snoozing a history insight follows its activity type;
a different best-performing activity can still appear. Existing priority and
score rules rank the complete candidate list before the display limit is applied.

Snoozes use a ten-minute elapsed-time deadline. Choosing the same snooze again
does not extend it. They are shared across this app's characters and retained
through Pause, Stop, Start and Reset Session, but **closing the app clears them**.
They do not mark an activity complete, alter a cooldown, update a business
reading or change saved earnings. At most 128 snoozes can be active; Restore all
or expiry makes room again.

Local tests cover stable action identities, deadline boundaries, ranking and
refill, session lifecycle and unchanged saved data. Opt-in Linux offscreen Qt
tests exercise the actual controls, reused-card keyboard/mouse ownership and
dashboard/overlay updates:
`GTA_RUN_QT_TESTS=1 QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_recommendation_snoozes_qt.py`
(set the variables separately on shells without inline assignments). Windows
rendering, screen capture and in-game availability remain unverified.

### Live Session Goals

Open **Session** and choose **Set Goal**. Pick a quick preset or enter a custom
earnings, activity or time target. The card shows progress and what remains;
the overlay displays the same goal. **Change Goal** includes the current
statistics period's existing totals, while Cancel leaves the goal untouched.
Use **Clear** to remove the target. The statistics below the card scroll, keeping
the controls reachable at the normal window size.

- **Earnings** count positive observed balance changes. Starting cash is excluded
  and spending does not subtract progress. For example, a balance sequence of
  $1,000,000 → $900,000 → $950,000 contributes $50,000 toward the goal, while the
  saved session's net balance change is −$50,000. Mission summaries do not add the
  same observed money again.
- **Activities** count all finished activities, including failed activities.
- **Time** counts whole elapsed session minutes, including paused capture time,
  and freezes when the session tracker stops. Reset Session starts a new
  statistics period under the existing reset behavior, even if capture is stopped.

The selected target is remembered in `session_goal_target.json` beside the app's
settings. **Progress starts fresh on Start, Reset Session and app restart.** Goal
progress and completion history are session-local; they are not a durable attempt
archive. A completed attempt keeps its first crossing until a new target or
statistics period is chosen. No ETA is shown because a target selected midway
through a session includes earlier progress.

Target changes are saved atomically; routine progress refreshes do not write the
file. A save failure leaves the current in-app selection usable and displays
**Retry Save**. Until retry succeeds, reopening may restore the previous saved
target. Invalid remembered targets stay untouched until Set or Clear is chosen.
Target files are limited to 16 KiB and names to 200 characters. Names display as
literal text. This per-file replacement does not provide crash durability or a
transaction across multiple app instances.

Local tests exercise actual app accounting/SQLite, goal lifecycle and persistence,
plus real offscreen Qt dialogs, MainWindow controls and overlay. Native goal tests:
`GTA_RUN_QT_TESTS=1 QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_session_goals_qt.py tests/test_session_goal_app_qt.py`
(set the variables separately on shells without inline assignments). Ordinary
pytest skips these opt-in UI tests. Linux offscreen rendering is verified;
Windows capture, OCR accuracy and interactive gameplay remain unverified.

### Cooldowns and Personal Reminders

Open **Activities → Manage cooldowns…** to manage the same timers shown in the
overlay. This works before capture starts. Start, Stop, Pause and Reset Session
retain the timers; closing and reopening the manager does not restart them.

- **Start timer…** offers the app's existing positive-duration presets and a
  **Custom timer**. Enter hours, minutes and seconds from 1 second to 7 days.
  Presets fill the app's saved default; changing this timer does not change that
  default. Check the duration against your game
- A custom name accepts 1–200 characters and displays as literal text. Custom
  timers have separate identities, so two reminders can share a name
- **Adjust…** sets the selected timer's remaining duration from the moment you
  accept. Cancel leaves it unchanged. Acceptance deliberately sets that same
  timer again even if it expires, is removed, or an automatic completion changes
  it while the editor is open. Custom names can be edited
- **Remove** clears only the selected timer. The list follows remaining-time
  order and removes expired entries. The compact overlay keeps its existing
  shortened names and display limit; the manager shows full names

Timers are personal reminders **shared across this app's characters**. They use
elapsed wall-clock time, including while capture or the app is stopped, and do
not establish in-game availability or change optimizer recommendations. Existing
successful-completion detection may restart the matching preset; failed or
unmapped activities do not invent a timer. Game durations and detection policy
are unchanged. Clock rollback retains the existing elapsed-time clamp.

The app loads `cooldowns.json` once when it opens and keeps one tracker for its
lifetime. Changes are saved with same-directory atomic replacement. If saving
fails, the in-memory reminder remains usable and the manager shows **Retry save**;
reopening the app may otherwise restore older timers. Retry saves the latest
state without restarting any countdown. A malformed or unreadable saved file
stays untouched merely by opening/refreshing the manager; the next successful
timer change can replace it. The manager discloses that recovery behavior.
This is per-file persistence with serialized access inside one tracker, not a
transaction across multiple app instances or a power-loss durability guarantee.

Local tests cover temporary real JSON/SQLite, controlled clocks and concurrent
tracker access, plus the actual MainWindow → editor → full list/overlay workflow.
Native tests: `GTA_RUN_QT_TESTS=1 QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_cooldown_manager_qt.py tests/test_cooldown_manager_app_qt.py`.
Ordinary pytest skips the opt-in UI tests. Linux offscreen Qt is exercised;
native Windows rendering, OCR accuracy and actual game cooldown timing are not.

### Saved Session History

Open the **History** tab to browse completed sessions, filter by character and
page through older records. Select a session to inspect its recorded activities
and balance changes, then choose **Export selected session as JSON…** to save it
where you want. The view works while tracking is stopped and does not start a new
session or change the active character. Use **Refresh history** to load newly
completed sessions; sessions still in progress are excluded.

Saved **Net balance change** includes spending, so it can differ from the live
gross-earnings counter. Times are shown in UTC and missing legacy values stay
unavailable rather than becoming zero. History pages contain 25 sessions; detail
tables show up to 1,000 rows each, while the JSON includes every recorded activity
and balance change for the selected session. Browsing does not edit the records.

Local tests cover real SQLite paging/filtering and the actual Qt tab, selection,
details, canceled/failed saves and JSON output. To run native UI checks with the
existing UI dependencies installed, use `GTA_RUN_QT_TESTS=1 QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_history_panel_qt.py`
(set the variables separately on shells without inline environment assignments).
The ordinary suite skips these opt-in Qt tests. Linux offscreen rendering is
validated; the native file chooser is substituted in tests, and Windows capture
and interactive game integration remain unverified.

### Daily Session Overview

In **History**, choose **Daily session overview…** to review saved completed
sessions by their UTC completion day. The dialog starts with History's selected
character and the last 30 inclusive UTC dates. Choose another character or date
window, then **Apply filters**. Its controls are independent of History's paging,
comparison baseline and session-note search.

Each day shows its session count, known recorded net change, positive elapsed
duration and paired hourly rate, with contributing counts. Every date in the
window is included. A day with no completed sessions has a zero session count
and unavailable numeric totals; it is not presented as an observed zero-dollar
result. Select a day to browse its captured source sessions, 25 per page, and
read the full selected record. This inspection uses the same snapshot as the
daily totals, even if the database changes afterward.

- Net change uses the stored session total, including losses and spending. It
  is not recalculated from opening/ending balances or added to activity amounts,
  earnings events, annotations or the live gross counter
- The whole session belongs to its normalized UTC completion date. Elapsed time
  comes from saved start/end timestamps and includes pauses and midnight
  crossings. Overlapping sessions are summed, so this is positive recorded
  session duration, not time actively played within that calendar day
- Missing or invalid starts leave the session and known net change visible,
  with unavailable duration. Known zero and negative durations remain visible
  but do not contribute to positive-time totals or rates
- Net change per hour uses only sessions with both known net change and positive
  duration. It divides their combined net by their combined elapsed time, rather
  than averaging individual rates or combining different row sets. The paired
  count makes that coverage explicit

Integer-only net totals remain exact. Mixed numbers and rates use exact
accumulation before conversion; a result outside finite numeric range, or a
nonzero result too small to represent, stays unavailable with an issue. Exact
elapsed microseconds are retained in the source rows. These are recorded
observations, not verified payouts, profit or future performance.

Both UTC dates are required and the inclusive window is limited to 366 days.
The complete selection supports at most 10,000 source sessions; exceeding it
asks you to narrow the window or character. A missing explicitly selected
character is unavailable. An unreadable non-null completion timestamp prevents
the scoped request, with its count shown, because date membership cannot be
determined safely. This also applies to malformed end values that appear to be
outside the window. Open sessions and rows without an existing character are
excluded. Naive saved timestamps use the existing UTC convention; valid offsets
are normalized before filtering and grouping.

Editing controls retires the old results and export until Apply. Refresh rereads
the applied selection; with unfinished edits it refreshes character choices and
keeps the draft unsubmitted. **Export snapshot as JSON…** saves all captured days
and source sessions, including filters, observation time, coverage and metric
notes. The file chooser retains that snapshot even if controls or stored rows
change. Cancel or closing the dialog during the chooser writes nothing. Reports
over 8 MiB UTF-8 are refused before staging, and failed writes preserve an
existing complete file. This is per-file recovery, not crash durability.

Local verification uses real temporary SQLite and Linux offscreen Qt, including
the MainWindow History → daily overview → source paging → export path. Native
checks: `GTA_RUN_QT_TESTS=1 QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_daily_session_overview_qt.py tests/test_daily_session_overview_app_qt.py`.
The ordinary suite skips these opt-in modules. Native file choosers are
substituted; Windows, game capture, OCR accuracy and interactive gameplay remain
unverified.

### Saved Session Notes and Tags

In **History**, select a completed session and choose **Edit session notes…**.
Give the run an optional short label, comma-separated tags and a personal note.
For example, label a solo heist run, tag it `solo, heist`, and record the route you
want to try next time. Save commits that context; reopening the same session
shows it again even while tracking is stopped.

Labels accept up to 80 Unicode characters, notes up to 4,000, and at most eight
tags of 1–32 characters each. Tags are trimmed and deduplicated without regard
to case, keeping the first spelling and order. Commas separate tags, so they
cannot be part of an individual tag. Label/tag controls are single-line; notes
preserve ordinary multiline text. All labels and notes display as plain text.
Saving an unchanged note, including a label/tag-only edit, retains its original
saved characters. Editing the note content normalizes paragraph breaks to line
feeds while retaining nonbreaking spaces.

Use History's annotation search to find a literal phrase in saved labels, tags
or notes, optionally with the existing character filter. Apply starts at the
first matching page. `%`, `_` and escape characters are literal. A–Z ignores
case; other letters match exactly, as in the activity ledger. Empty search shows
all completed sessions. Editing the query clears the old rows/details/export
until it is applied; it does not alter an already-open note editor's session.
The comparison baseline remains independently pinned.

The editor owns the session ID selected when it opened. Changing History's row,
page or character cannot retarget its save. **Clear draft** changes the editor;
it only clears saved context after Save. Cancel or closing the editor writes
nothing, with a discard choice when a draft has changed. A failed save preserves
the draft for retry. If another app instance has changed that note, the stale
save is refused; explicitly reload the saved version before deciding what to
keep. Reload warns before replacing a changed draft. A successful save is
acknowledged separately if History's subsequent refresh fails.

Annotations live in a separate `session_annotations` table in the same SQLite
database. Existing databases acquire that additional table on initialization;
captured session, activity, earnings and character rows are not rewritten. Each
save validates that its session still exists and is completed, and commits all
annotation fields together using a revision check. Clearing keeps an empty
versioned record so an old editor cannot overwrite a newer clear as a first save.
These are personal observations, not verified game events or changes to recorded
money, activity outcomes, goals or live tracking.

**Export selected session as JSON…** adds saved annotation context when present.
The existing export captures the selected session ID before opening the chooser
and reads its saved values afterward; it never exports an editor's pending draft.
Thus a later committed note for that same ID can be included. Unannotated exports
retain their existing shape. Corrupt annotation data is marked unavailable while
captured session/activity/balance records stay readable and exportable; it is not
silently replaced with empty notes. Database failures remain visible failures.
An export failure preserves an existing complete destination through the existing
per-file staging writer, without a crash-durability guarantee.

Local tests use real disposable SQLite for additive initialization, competing
first saves and edits, clear/revision ownership, malformed data and literal search.
Actual Linux offscreen Qt tests cover MainWindow → later-page session → edit →
search → reopen → JSON export, selection changes, failures and long literal notes.
Run native journal checks with
`GTA_RUN_QT_TESTS=1 QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_session_annotation_qt.py tests/test_session_annotation_app_qt.py`.
The ordinary suite skips these native modules. Windows file dialogs, capture,
OCR accuracy and interactive gameplay remain separate platform checks.

### Compare Completed Sessions

In **History**, select a completed session and choose **Use as baseline**. The
visible baseline A stays pinned while you change character filters or browse
other pages. Select a different session B and choose **Compare with baseline**;
use **Clear baseline** or pin another session to change A.

The comparison shows both session IDs, characters and saved UTC times, followed
by stored net balance change, elapsed duration, net change per hour, and recorded
activity/outcome counts. A second table compares the actual recorded activity
types. Every difference is **B minus A**; a positive difference is not an
automatic improvement. Cross-character comparisons are allowed and clearly
labeled.

Net change is the stored session total, even if a legacy record disagrees with
its opening and ending balances. It includes spending and is separate from live
gross earnings, recorded activity amounts and positive balance events. Those
overlapping records are not added together or used to infer complete spending.
The activity comparison counts records and passed/failed/unknown outcomes; it
does not estimate game payouts. Aggregates include all recorded activities,
independently of the 1,000-row detail display limit.

Missing values stay unavailable, while known zero and negative values remain
visible. Duration comes from saved start/end timestamps and includes elapsed
paused time; anomalous zero/negative durations are retained but have no hourly
rate. Rates require known net change and positive duration. Closed database
sessions, rather than in-memory goal/statistics resets, define the compared
periods. Opening a comparison rereads the exact selected IDs without starting
capture, creating a session, changing the active character or editing history.

The dialog is a fixed snapshot. **Export comparison as JSON…** saves that same
pair and displayed values, including raw numbers/nulls, type counts, orientation
and metric notes. It does not switch to a later History selection or reread an
edited source while the file chooser is open. Close and compare again for fresh
data. Cancellation writes nothing; a failed save preserves an existing complete
destination and allows retry. Replacement is per file, not crash durability.

Local verification uses real SQLite and actual Linux offscreen Qt controls,
including the MainWindow History flow, pinned selections across filters/pages,
failed saves and snapshot exports. Native Windows file dialogs, capture, OCR and
interactive gameplay remain separate platform checks.

### Recorded Activity Ledger

In **History**, choose **Browse recorded activities…** to inspect individual
records across completed sessions. The modeless ledger starts with History's
character filter and then keeps its own controls. It works while tracking is
stopped and does not start capture, create a session, switch characters or edit
history. The existing live **Activities** tab remains a view of recent tracking.

Filter by character, optional inclusive UTC From/Until dates, exact stored
activity type, Passed/Failed/Unknown outcome, or a literal phrase in activity
names and notes. Choose **Apply** to load the first page. Empty text fields mean
all; type matching is exact, while name/notes search ignores A–Z case and matches
other letters exactly. `%`, `_` and escape characters are literal, not search
patterns. Phrases accept up to 200 characters without controls or line separators.
Null, blank and custom type values remain visible when viewing all types.

Pages contain 25 records in newest activity-time order, with activity ID breaking
ties. Activity time uses recorded completion time, falling back to recorded start;
undated records appear last without date bounds and are excluded when a date
boundary is active. Recorded start can be a persistence-time default, so it is
not reconstructed gameplay start. Duration is the stored measurement and is not
recomputed from those timestamps. Recorded amounts are separate from session net
balance change and do not establish verified payouts or profitability.

Passed and Failed use the stored true/false values; missing or other legacy
outcomes are Unknown. Missing or invalid numeric values stay unavailable, while
known zero and negative amounts/durations remain visible. Open sessions and
orphaned rows without an available session/character are excluded. A missing
outcome or timestamp does not imply cancellation or current gameplay activity.

Select a row to read its saved details as plain text. Editing a filter clears the
old page, details and export until Apply. Refresh reloads the applied selection;
with unapplied edits, it refreshes available characters while keeping the page
retired. Each query obtains its matching total and bounded page in one SQLite
statement. Different pages or refreshes can observe later writes; this is not a
permanent multi-page archive. Counting/filtering may scan the database even
though the returned page is bounded.

**Export this page as JSON…** saves the accepted page, including applied filters,
observation time, total/page counts, raw numbers/nulls, saved fields and explanatory
notes. It exports this displayed page only. Changing the database or controls
while the file chooser is open cannot switch that captured export. Cancellation
writes nothing, and failed writes preserve a previous complete destination.
Reports over 8 MiB of UTF-8 JSON are refused before staging. File replacement is
per-file recovery, not crash durability or a database transaction.

Local verification uses real disposable SQLite, actual Linux offscreen Qt controls
and the MainWindow History → ledger → export path, including literal/long data,
filter retirement, paging, failed saves and snapshot ownership. Native checks:
`GTA_RUN_QT_TESTS=1 QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_activity_ledger_qt.py tests/test_activity_ledger_app_qt.py`.
The ordinary suite skips these opt-in Qt modules. Windows file dialogs, capture,
OCR accuracy and interactive gameplay remain separate platform checks.

### Historical Activity Insights

Open **History → Activity insights…** to compare recorded activity types across
completed sessions, including older records outside the live tracker's recent
history. Choose a character, inclusive UTC activity-date bounds, outcome, or
literal name/notes phrase, then **Apply filters**. The table groups exact stored
types, including custom names, and shows activity/session counts, outcome and
numeric coverage, recorded amounts, measured durations and paired rates.

- **Passed / known outcomes** excludes unknown outcomes from its percentage and
  keeps their count visible. Failed activities remain in amount/duration metrics
  unless you explicitly filter them out
- **Known recorded amount** includes finite zero and negative amounts. Missing,
  nonnumeric and nonfinite values remain unavailable; totals and means disclose
  their known sample count. These are stored activity amounts, separate from
  session net balance changes and earnings-event totals
- **Positive measured duration** uses stored seconds greater than zero. Zero,
  negative and unavailable durations have separate counts in the selected
  detail. Durations are not reconstructed from timestamps
- **Recorded amount per measured activity hour** uses only rows with both a
  known amount and positive finite duration: their amount sum divided by their
  duration sum, multiplied by 3,600. It is not the mean of individual rates, a
  session-wide earning rate, verified payout/profit, or a future prediction

Select a type for its full coverage details and numeric issues. Exact integer
sums are preserved; mixed finite values accumulate before final conversion.
Unrepresentable totals/means/rates remain unavailable with an issue, including
nonzero values too small to represent. An empty result is distinct from known
zero amounts. Results cover the complete matching selection, with limits of
100,000 source activities and 256 exact types. Exceeding a limit asks you to
narrow the filters; no silently partial summary is shown. Unsupported stored
activity types are reported instead of being folded into another type.

**Browse matching activities…** opens the existing activity ledger with the accepted
character/date/outcome/search/type filters. This reads current matching records,
so later database changes may differ from the earlier summary. Missing, blank,
or control-containing exact types show their summary but disable this
shortcut rather than opening all activities. An already-open ledger keeps its
original selection until closed. The insight dialog starts with History's current
character; subsequent History changes and its separate session-note search do
not retarget it or the independently pinned comparison baseline.

Editing insight filters clears the accepted results and disables drill-down and
export until Apply. Refresh rereads applied filters and keeps an unapplied draft
explicit. **Export summary as JSON…** saves the accepted dated summary, coverage,
filters and metric notes without rereading the database. A file chooser opened
for an earlier snapshot keeps that captured export even if the view changes.
It exports grouped summaries, not original activity rows. Serialization is
bounded to 8 MiB and uses the existing atomic per-file replacement; cancellation
writes nothing and a failed replacement preserves an existing output. This is
not a multi-file transaction or a power-loss durability guarantee.

Insights require an existing completed session and character for each activity.
Activity time is completion with recorded-start fallback, using the ledger's UTC
rules; undated rows appear only without date bounds. The query reads a narrow
single-statement observation and does not alter captured records, active
character, tracking or live analytics. Stored start times may be persistence-time
defaults. Each Refresh and ledger opening is a new observation.

Local verification uses temporary real SQLite records, concurrent WAL writes,
extreme/missing numeric cases and actual offscreen Qt MainWindow, filters,
ledger and export paths. Native tests:
`GTA_RUN_QT_TESTS=1 QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_activity_insights_qt.py tests/test_activity_insights_ledger_qt.py tests/test_activity_insights_app_qt.py`.
Ordinary pytest skips these opt-in UI cases. Linux offscreen rendering is tested;
native file choosers are substituted, and Windows/game capture is unverified.

### Activity Detection

The capture pipeline now shares one mission-identity result from the existing
mission-text, center-prompt and mission-banner OCR crops through tracking,
dashboard/overlay labels, completed history and
cooldown selection. For example, **Hostile Takeover**, **Asset Recovery** and
**Executive Search** retain their VIP category; a center-only **Headhunter**
retains its name; **Customer vehicle** identifies Auto Shop delivery instead of
winning a generic sell-keyword match. Recognized catalog names appear in the
dashboard and overlay, while the state badge and timer remain separate.

Recognition uses canonical names already in the repository's contact, VIP,
security-contract and other named-activity lists, plus explicit category labels.
It normalizes case and whitespace, including wrapped names such as
`Executive\nSearch`, and requires word boundaries. Generic objectives cannot
outscore a specific name: **Headhunter** with **Deliver the goods** remains VIP
work. Explicit nightclub-promotion labels have their own activity category.
There is no fuzzy spelling correction, model inference, new payout estimate or
guarantee that every GTA mission is supported.

Each OCR crop now contributes its own complete phrases. **Executive** in one
region and **Search** in another cannot invent **Executive Search**; the same
boundary applies to category markers, delivery objectives and result phrases.
Line wrapping inside one crop still works. Complete agreeing evidence can still
combine across crops, such as **VIP Work** plus **Hostile Takeover**, or **Casino
Heist** plus an independent **Finale** label. An incidental phase word in another
crop cannot borrow a heist family's context. The parser retains the original
region text and chooses the first per-region objective without appending words
from another crop. A phrase clipped across separate crops can therefore remain
unresolved until one crop contains complete evidence; there is no positional
word stitching or spelling inference.

When **take** is the only generic active keyword, it now requires a complete
imperative from the existing per-crop objective evidence. **Take the briefcase**,
**Objective: Take the briefcase** and **Take out the guards** retain the generic
mission score of `0.7`; wrapping inside one crop is supported. Bare **Take** or
**Take out**, result-table labels such as **Secondary Targets Take:**, embedded
prose and fragments split across separate crops cannot trigger that generic
branch. An unrelated **Bring the briefcase** command cannot validate a table's
**Take** label. Specific mission identities, explicit results, delivery rules
and the other existing generic keywords retain their priority and behavior.

This narrow rule prevents the demonstrated result-table false start. It does
not recover an unread heist title/result heading or change the capture crops.
It conservatively refuses a take objective preceded by OCR noise such as
**Score 42**, or punctuation such as **Take, the briefcase**. Text regressions,
actual app/tracker/SQLite checks and opt-in synthetic-image Tesseract tests verify
these boundaries; they do not measure gameplay recall or accuracy. A strong
existing generic banner template can still start an unresolved activity.

A fresh local Tesseract check of one public-guide Cayo result screenshot also
prevented the false activity start. Its unchanged visual layer still returned a
weak `MISSION_ACTIVE/0.6`; the existing application threshold rejected it. The
[provenance and evaluation record](tests/fixtures/ocr_take_result_table_evaluation.json)
keeps the source URL, fixed pipeline and bounded result without including pixels.
This diagnostic used no loaded templates. It does not establish native Windows
OCR performance or accuracy across gameplay sequences.

Conflicting names/categories remain ambiguous. Unknown objectives no longer
default to a contact mission; a generic delivery classification remains
unresolved until stronger identity evidence arrives. A visual-only result at
the unaccepted confidence threshold cannot start tracking. A later clear name
can improve an unresolved activity without resetting its time or money baseline,
adding another activity, or counting income twice. Once established, a name or
category is not replaced by incompatible later text.
If the first clear title appears on an accepted result banner, it can refine an
existing compatible activity before that activity is recorded. A result banner
alone never creates a new activity.

An explicit result with an incompatible known name, family or prep/finale phase
is now rejected before changing the app's state or classifying a balance event.
For example, after **Headhunter** ends and **Sightseer** starts, another named
Headhunter result cannot complete Sightseer, record its outcome, notify completion
listeners or start its cooldown. The capture reports an uncertain state and a
reason while retaining the OCR text and current activity. Actual balance changes
are still observed. A later compatible result can complete the original activity
with its original time and money baseline. Unnamed results and compatible late
identity refinement keep working; ambiguous identity retains its prior behavior
and does not establish an explicit name/family/phase conflict. This is an evidence
compatibility check; the post-result handoff below separately handles replayed
identity evidence.

After a result, the app retains that finished mission's identity for the current
capture session. A title-only **Headhunter** frame cannot immediately restart
Headhunter just because OCR missed **MISSION PASSED** or **MISSION FAILED**.
That also lets a genuinely different **Sightseer** title start with its own
identity instead of inheriting a duplicate Headhunter activity. Capturing an
already-visible named result establishes the same context without inventing a
completion record.

A compatible title/category can start again when a new supported imperative
objective is observed. Comparison uses all clean objective evidence from the
result crops and the latest nonempty accepted active observation. Reordering crops, dropping one
of several objectives, changing line wrapping or losing result/title labels does
not by itself establish a fresh objective. The existing display objective and raw
OCR text remain available. Rejected reactivation reports an uncertain capture
state with a reason and does not create another tracker, ledger row, completion
callback or cooldown.

This is a conservative observation policy. If a legitimate same-name retry shows
exactly the same objective evidence, it can remain uncertain. Extending or
shortening an already-seen command alone also remains uncertain; an old objective
that OCR previously missed can appear new. Blank frames, elapsed time,
cooldown expiry and dark/menu/loading heuristics do not prove a new attempt.
Statistics reset and pause/resume keep this context; starting a fresh capture
session clears it. Up to 128 distinct normalized objectives are retained; if that
bound is exceeded, objective-based release is disabled until a distinct identity,
a trusted explicit start state or a fresh capture session. The current detector
does not produce that explicit start state. Unknown/ambiguous results cannot
invent an identity, and existing unknown direct-callback activity starts retain
their behavior. Strong generic banner detection can still start an unresolved
activity even with ambiguous OCR text; that existing behavior remains outside
this shared-identity guard. This does not prevent every unnamed replay or
identify every real gameplay attempt.

Heist family, specific name and explicit prep/finale phase are kept separately.
A previously missing compatible phase can be filled in: **Casino Heist**, then
**The Big Con / Finale**, can become a named finale while retaining the same
activity baseline. A confirmed prep/finale is not switched to the other phase
without the existing mission-result lifecycle. Approach names, `take`, `cut`,
locations and vehicle names do not by themselves establish a finale.

Mission results require explicit accepted result text or a result-template
match. Yellow/red pixels alone cannot finish an activity. A generic mission
banner cannot override explicit **MISSION PASSED/FAILED**, and contradictory
result text or a text/template result disagreement remains unconfirmed. Generic
bonus/reward text, **Objective complete**, **SUCCESS**, **COMPLETED** and **Well
done** do not end the whole tracked mission. These are heuristic recognition
rules, not calibrated confidence scores or proof of actual gameplay state.

An explicit result and one supported canonical mission title can also share a
single OCR segment in either order, such as **MISSION PASSED Headhunter** or
**Headhunter MISSION FAILED**. The same bounded rule supports **JOB COMPLETE**
and **CONTRACT COMPLETE**, with case and whitespace normalization. It retains
the title for the existing result-ownership checks, including the otherwise
imperative-looking **Blow Up** title. An accepted result still cannot start a new
activity. This prevents that layout alone from inventing a mission or leaving the
previous mission active when a different one appears.

The new rule requires the complete title/result structure within one crop.
Unknown titles, extra prose, competing titles and separate-crop fragments are
not completed by guessing. Ordinary **blow up the vehicle** objectives, bare
status rules, existing payout handling and contradictory-result checks keep
their separate behavior. Mixed title/reward text outside those existing rules
can remain unresolved. This does not establish how often native OCR produces
the supported layout in real gameplay.

The normal capture batch now includes the already-defined mission banner.
Previously its upper title rows were outside both consumed OCR crops, so a
readable name there could never reach automatic selection. Banner text now
participates in the same identity, phase and result checks, while original text
from all three regions remains separate in capture metadata. For example, a
generic **VIP Work** header plus **Hostile Takeover** in the banner can establish
the named activity; contradictory names in different crops remain ambiguous.
An accepted banner result can finish an existing activity, but does not create
a new one by itself. Coordinates are unchanged.

The full-screen visual checks, money, mission objective, center prompt, timer and
mission banner now come from **one native screen grab per detection cycle**.
Previously the six separate grabs could combine text from different instants,
especially where the center and banner crops overlap. Cropping one observation
prevents that source of contradictory titles/results or a name assembled from
words that never appeared together. It does not resolve contradictory evidence
already present in one screenshot or change the mission-recognition rules.

The batch grabs the enclosing rectangle and returns independently writable BGR
crops in the original order, preserving pixel rounding and monitor offsets.
An invalid or empty region retains a missing-image entry. If the shared grab
fails or returns the wrong dimensions, the batch returns missing images rather
than retrying regions against a later screen. It waits once and paces failed
attempts; an empty batch does no capture work. Standalone captures and the later
business-computer reads keep their existing scheduling. One grab is not a
guarantee against operating-system/compositor tearing, concurrent configuration
changes, or a measured Windows latency, memory-use or FPS improvement.

The Windows adapter now follows [WinOCR's lowercase Python API](https://github.com/GitHub30/winocr#information-that-can-be-obtained)
and preserves backend line text, punctuation and word bounds. Windows
[OcrWord exposes no confidence score](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrword),
so successful native results/words report confidence as `None`, rather than an
invented certainty. Empty/error results retain the existing empty-text/0.0
failure convention. The package requirement now agrees with the existing
requirements file and the published `winocr>=0.0.15` API.

Before native recognition, the adapter reads Windows OCR's runtime
[maximum image dimension](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine.maximagedimension).
Only oversized images are reduced to fit that actual limit, preserving their
aspect ratio within integer pixel rounding and keeping the full image. This
prevents enlarged high-resolution crops from being submitted above the engine's
size contract; it does not assume a fixed Windows limit or establish recognition
accuracy. Successful word bounds are mapped back to the image supplied to
`recognize()`; for preprocessed calls this remains the preprocessed coordinate
system. An unavailable/invalid runtime limit or resizing/backend error retains
the existing empty-result failure behavior. In-limit images and native line
text remain unchanged.

Local verification covers faithful Windows API-shaped responses, source-derived
OCR text through the real capture/classifier/tracker/SQLite path, result and
identity ownership, and actual offscreen Qt labels. An additional opt-in suite
renders controlled text at 720p/1080p/1440p and runs production crop/preprocessing
code through an already installed Tesseract CLI and the real capture pipeline:

```bash
GTA_RUN_OCR_TESTS=1 python -m pytest -q tests/test_mission_ocr_images.py tests/test_capture_snapshot_ocr_images.py tests/test_mission_crop_ocr_images.py tests/test_mission_episode_ocr_images.py tests/test_inline_mission_result_ocr_images.py
```

That optional diagnostic requires Tesseract with English data, DejaVuSans.ttf,
Pillow, NumPy and OpenCV; it installs/downloads nothing. `GTA_OCR_TEST_FONT` may
point to a local copy of that font. It adds no production Tesseract fallback.
Banner cases place known titles wholly in the previously unread part of the
configured region, and cover competing crop evidence, late refinement, explicit
heist phases and single completion/cooldown records. Separate native-shaped
backend tests enforce a deliberately synthetic size cap across ordinary and
high-resolution inputs and verify bounds mapping; they do not execute Windows
OCR or claim that the test cap is the native maximum.
The changing-screen integration uses actual batch capture and Tesseract with
complete rendered frames that switch between native-grab calls. Stable-frame
controls and three transition cases cover contradictory titles, a false
**Executive Search** assembled across frames, and conflicting PASSED/FAILED
results through the real activity and SQLite paths. These demonstrate the
pipeline defect and correction on controlled inputs, not its gameplay frequency.
Result-episode image cases additionally cover pass/fail title dropout, a distinct
next mission, unchanged objectives surviving other text, a fresh same-name
objective and capture starting on a result screen. These generated frames are
not GTA screenshots and do not establish native Windows OCR or gameplay accuracy.
The test images are synthetic, not game screenshots. This repository ships
no representative gameplay screenshot corpus or template images; default startup
also does not automatically load a template folder. This pass does not change
crop placement. Actual Windows OCR execution, real
HUD/font/layout variations, motion, lighting and gameplay false-positive rates
remain unverified. A catalog label visible in a menu is still a possible
recognition cue; the tests do not prove that the player has started that mission.

Additional controlled 720p/1080p/1440p images cover separate fragments that used
to invent names/categories/results, wrapped-name and agreeing-crop controls, and
a named old result returning after another mission starts. These execute real
Tesseract, production preprocessing/crop coordinates, detection, activity tracking
and temporary SQLite. Text-based capture cases also check state/money callbacks,
history, cooldowns and later valid completion. The checks establish these pipeline
boundaries on controlled inputs, not real-game accuracy or native Windows behavior.

Inline-result diagnostics additionally render both title/status orders at
720p/1080p/1440p and assert the actual Tesseract transcription before checking
capture, tracker, balance and SQLite behavior. They cover initial result screens,
the next mission's identity, repeated and mismatched results, and conservative
prose/unknown/crop-boundary controls. Actual balance changes remain observed;
the correction prevents phantom tracked activities and preserves existing
accounting policy. These generated frames do not substitute for representative
gameplay screenshots or establish Windows OCR accuracy.

#### Heist success labels and real-image evidence

When OCR returns **HEIST PASSED** together with unambiguous supported heist
identity or an explicit finale label, the app can complete a compatible tracked
activity. A standalone unqualified label, contradictory evidence, or an explicitly
tracked prep/non-heist activity remains uncertain. The phrase must be complete
within one OCR crop; this does not infer a finale phase or a payout, and a result
screen alone does not start a new activity.

Five separately published guide screenshots were also inspected and evaluated
locally. Their [text-only provenance and baseline results](validation/gta-online-screenshot-baseline.json)
record the source URLs, image hashes/dimensions and exact crop coordinates. Those
baseline regions missed visible bottom objectives and the upper Cayo result
heading. At that stage, wider-crop/non-thresholded diagnostics recovered the Cayo
title but missed the large result heading. The later dedicated result-header
observation below recovers that retained summary with a different, fixed crop
and preprocessing pass; the original baseline remains recorded unchanged.

The images and derivatives are kept outside the repository because redistribution
rights were not established. Two guide images are cropped; the three 16:9 Cayo
images still have unknown original capture and HUD settings. This small sample
is not a Windows OCR accuracy benchmark or a gameplay transition test, and it
does not replace the still-pending YouTube screengrab work.

#### Recognize a named bottom objective

The capture cycle also reads a narrow bottom-center objective region from the
same screenshot. A clean instruction such as **Escape Cayo Perico.** can now
supply the existing Cayo family classification when the upper crops miss it.
The activity displays the observed instruction; this does not invent a mission
title, finale phase, successful result or payout. The accepted instruction also
participates in the existing completed-mission replay guard, so the same old
objective cannot immediately restart that mission.

This additional region has a restricted role. It must contain one complete
instruction with an unambiguous supported mission family. Generic instructions,
bare names, numbers, currency, explicit result text and standalone **RP**,
**Platinum** or **Continue** rows do not supply new activity evidence through
this region. Extra text can
make the observation unusable. Recognized business screens and existing mission
results retain their original processing, and conflicting mission identities
remain uncertain. Bottom text is never used as a business reading, balance or
result source.

The extra read uses one fixed grayscale, inverted, 2× preprocessing pass. It
does not expand the upper crops or change their preprocessing. This conservative
rule can miss valid objectives containing counters, numeric destinations or
noisy footer text, and it does not prove that every possible result-table layout
is excluded. The extra OCR call also adds work to eligible capture cycles.

Controlled capture/accounting tests and optional generated-image tests exercise
these boundaries at 720p, 1080p and 1440p. Run the image checks with the existing
local Tesseract diagnostic dependencies:

```sh
GTA_RUN_OCR_TESTS=1 python -m pytest -q tests/test_bottom_objective_ocr_images.py
```

In the earlier bottom-only evaluation, the five supplemental guide stills were
replayed through the actual capture, preprocessing, detector and application
path. The escape instruction started a Cayo-family activity; the other four
isolated frames started no activity. Playing the escape frame followed by the
real summary left the activity unfinished because the result heading remained
unread. No completion or payout was fabricated. Those observations are retained in
[the bottom-objective evaluation](validation/bottom-objective-evaluation.json).
These are diagnostic Tesseract results, not Windows OCR or live-game accuracy
measurements. The guide images remain outside the repository.

#### Recognize a qualified upper heist result

The same capture batch also supplies a separate upper-center result header.
It uses the fixed relative region `(0.20, 0.12, 0.60, 0.20)` with grayscale,
no thresholding or inversion, and 2× scaling. This preserves the existing
mission and bottom-objective crops. It adds an OCR pass on eligible cycles;
native performance and broader layout coverage have not been measured.

This observation can supply a result only when its own text contains a complete
**HEIST PASSED** phrase and an unambiguous supported heist family. A title alone,
business label, objective, number or currency cannot start or refine an activity,
open a business reading, or supply a balance or reward through this region.
A heading that loses its heist title cannot borrow identity from another crop
or from the currently tracked activity. Prep-qualified, ambiguous or conflicting
header evidence remains uncertain. Existing primary business/result decisions
and compatible activity ownership checks retain their authority.

A qualified result can complete the original compatible activity once, retaining
its start time and already established phase. A result seen while idle creates
no activity. Repeated results and the old objective remain subject to the
existing episode fence. Raw header text is retained separately from admitted
result evidence and never becomes a fresh objective or payout estimate.

The retained real Cayo summary is now readable through this dedicated pass.
In a manually arranged replay of the guide's escape and summary stills, the
actual app completes that same Cayo owner exactly once, with no inferred payout.
At that evaluation stage, the other original images retained their prior activity behavior. Generated
controls cover mismatched owners, title dropout, contradictory evidence and
business readings; they are not additional gameplay screenshots. Exact source
hashes, OCR observations and limits are recorded in
[the result-header evaluation](validation/result-header-evaluation.json).

This is one observed result layout using the local Tesseract diagnostic adapter.
It does not establish general recall across heists, languages, HUD safe zones or
aspect ratios, and it does not validate Windows OCR, live gameplay or YouTube
capture. The original guide images and their derivatives remain outside the
repository because redistribution rights are unestablished.

#### Recognize the distinctive El Rubio objective

The complete instruction **Go to El Rubio's compound.** can identify the Cayo
Perico family even when no literal Cayo title is readable. This is one explicit
objective-to-family association supported by the retained Cayo guide image and
its context. The pixels do not print a Cayo title, and the parser does not insert
one into the OCR text. It keeps the observed instruction, reports family-only
identity, and leaves the mission title and heist phase unknown.

The command must occupy an entire independent OCR crop, apart from case,
whitespace and terminal sentence punctuation. Bare landmark words, generic
navigation, fragments, surrounding prose and unrelated trailing text do not gain
this authority. Existing bottom-region number, currency and result/footer vetoes
still apply. Conflicting identities remain uncertain, primary business/results
keep their priority, and an unqualified result header cannot borrow this family
from the objective. No capture geometry, preprocessing or OCR pass was added.

The five unchanged retained guide originals were evaluated again through the
local Tesseract, capture, detector and application path. The start objective now
starts a Cayo-family activity; the other four isolated images retain their prior
behavior. In an explicitly arranged escape → summary → start → escape → summary
still-image replay, the distinct start command establishes a second activity
owner. Its later summary completes that same owner once, preserving its start
time and producing no inferred earnings, money rows, cooldowns or business data.
This arrangement is not evidence of actual two-heist gameplay chronology.

Exact provenance, OCR observations, ownership and accounting checks are recorded
in [the distinctive-objective evaluation](validation/distinctive-objective-evaluation.json).
Generated images and injected text remain separate regression evidence. In the
unchanged synthetic 720p fixture, local Tesseract reads **El** as **EI]** and the
exact rule leaves it unidentified. The corresponding 1080p and 1440p fixtures
are readable; this is a measured fixture limit, not a resolution guarantee.
This single signature does not establish wider precision or recall, other languages,
OCR substitutions, HUD settings, non-game scenes, Windows OCR, live gameplay or
YouTube capture. The original images and derivatives remain outside the repo.

#### Recognize the VIP work status label

An additional lower-right HUD crop can identify **VIP Work** from a complete,
spaced **VIP WORK END** status row. It also accepts the exact joined label
**VIPWORKEND** when followed by a separate, bounded numeric-like suffix, as in
**VIPWORKEND 11:324**. This supplies activity type only: it does not name
Headhunter or Sightseer, infer a mission phase, or interpret the suffix as a
valid timer. **END** beside a live countdown does not mean the
mission has ended. A later independently readable title can refine the same
activity without restarting it.

Admission is bounded and requires the whole label row. The joined form requires
at least one space or tab before the suffix; a bare joined label, partially
joined words, clipped labels, letter substitutions and surrounding prose remain
rejected. Only the fixed **VIP WORK**
marker enters identity matching; other footer text cannot supply objectives,
outcomes, balances or earnings. Conflicting families remain uncertain, business
and qualified result evidence retain priority, and an unqualified result header
cannot borrow this activity type. A changing or disappearing countdown does not
complete or rearm an activity.

The original five-guide evaluation used the actual local Tesseract, capture,
detector and application path. It recovered a type-only **VIP Work** activity
from the Headhunter screenshot where the earlier detector selected
**PHONE**. Its actual OCR suffix is **1273.**, which remains uninterpreted.
At that baseline, Sightseer's **VIPWORKEND 11:324** remained rejected. All three
Cayo observations and a separately arranged start → escape →
summary replay retained their prior behavior, including one completion and no
inferred accounting.

That original status-crop addition used one extra OCR pass from the same frame.
Across its eight evaluated observations, calls increased from 52 to 61: eight status reads
plus an existing timer read newly enabled by the recovered active mission.
[The VIP status evaluation](validation/vip-status-evaluation.json) records exact
source hashes, raw OCR, source isolation, ownership and costs. It remains the
historical baseline rather than being rewritten for the joined-label rule.
These are bounded observations using cropped guide images, not general recognition
accuracy. Windows OCR, live gameplay, other HUD layouts and the requested
YouTube-frame validation remain unverified. The originals and derived images
remain outside the repository.

The [joined-label evaluation](validation/joined-vip-status-evaluation.json)
freshly compares the actual diagnostic CLI on all five originals before and
after the narrow row change. Sightseer's guide frame changes from **MENU** with
unknown identity to **MISSION_ACTIVE**, unnamed **VIP_WORK**, with an unknown
phase and no outcome. The other four detector candidates stay identical.
All 30 crop observations match exactly, including crop/preprocessed bytes,
Tesseract inputs and outputs, text and confidence. A plain CLI run matches the
recorded run byte-for-byte. No image or derived crop is included in the repo.
The detector adds no OCR request; recognizing an active mission can enable the
application's existing conditional timer read. In the isolated capture/application
check, the formerly missed joined row changes from seven to eight OCR calls;
both accepted label forms use the same single frame grab and nine crops.
Episode/ownership checks keep footer text from creating a named mission,
completion or financial entry, including when the suffix is **0:00**.
Native Windows OCR and the
requested YouTube evidence remain unverified.

#### Recover an unfinished detection

If a result was missed while tracking was paused, the app can remain attached to
the previous activity. In **Activities**, choose **Pause capture**, wait for the
current capture to finish, then choose **Discard detected activity…**. Check the
activity named in the confirmation. Cancel leaves it intact; accepting drops
only that unfinished detection and keeps tracking paused. Choose **Resume
capture** when ready for a fresh observation.

Discard records no completed or failed activity and starts no cooldown. Session
earnings, recorded balance changes, completed history, existing timers, goals and
business readings remain intact. Its confirmation cannot discard an activity
that was replaced or refined, or survive an intervening Resume or capture. The
action is unavailable while capture is running, draining, stopping or stopped;
it never waits for OCR while holding up the interface.

After recovery, a new activity needs accepted activity evidence and a valid
balance **in the same fresh capture**. Until both are available, the panel shows
that it is waiting and no new activity estimate is started. The existing money
validation still rejects suspicious readings, including values below $100. A
hidden or rejected balance therefore delays recovery rather than reusing the
old activity's balance. The new estimate covers only observations after that
fresh start; it cannot reconstruct the missed result or the full gameplay payout.

The old selected name, timer and objective are cleared. The generic game-state
badge can retain its last observed state until another capture updates it. This
is an explicit recovery control; a different title alone still cannot silently
replace a tracked activity. Reliable automatic handoff after an unobserved result
remains dependent on representative gameplay transition evidence.

Local checks exercise the missed-result sequence through actual detection,
tracking and temporary SQLite, plus worker admission, paused OCR/callbacks,
stale confirmations and fresh-balance gating. Opt-in native controls are tested
with `GTA_RUN_QT_TESTS=1 QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_detection_recovery_qt.py`
(set variables separately on shells without inline assignments). These use Linux
offscreen Qt and synthetic OCR inputs. Windows capture and real gameplay accuracy
remain unverified; this adds no gameplay screenshot evidence.

### Business Management
- Track stock and supply levels for all businesses:
  - MC Businesses (Cocaine, Meth, Cash, Weed, Documents)
  - Bunker
  - Nightclub
  - Agency
  - Acid Lab
  - And more...

### Choose a Business Screen Target

In **Businesses**, use **Business screen target** when the OCR text does not
identify the business you are viewing, or its automatic text match is wrong.
Choose **Automatic** or one of the eleven supported business-card names. The
next detected business-screen reading is assigned to your explicit selection,
even if its text mentions another business. Check that the selection matches
the screen, and change it or return to Automatic when visiting another business.

The normal GUI starts tracking automatically. You can change this selection
while tracking or paused; selecting a target does not itself start capture or
create a saved record. It survives tab changes and pause/resume, stays only in
memory, and clears on **Stop** or application exit. A stopped app can also hold
a preselection for its next Start through the app API. The selector reflects
programmatic changes through the existing window/panel refresh, with no new
tracking controls or background service.

Each accepted live card keeps its own source label: **Selected target** or
**OCR text match**, alongside its status and update age. Changing the selector
does not move, clear or relabel earlier observations. If the choice changes while
OCR is processing a business batch, that batch is discarded before parsing and
publication, including a change away and back to the same target. Stop also
retires pending batches. The next normal capture uses the new selection.

The target assigns identity; it does not verify the screen or improve OCR
accuracy. Existing business-screen detection and numeric parsing still apply.
For example, `Stock: 5/10 Supplies: 3/4 Value: $123,456` can be assigned without
a business name. Bare `5/10 3/4 $123,456` is not newly supported. Missing fields
remain unknown; a missing value is estimated only when stock is known, and an
observed `$0` stays zero. Only cataloged businesses receive live cards; use the
target selector when Automatic cannot assign a supported business. Verify the
readings against the game. Cross-character live-cache behavior is unchanged. **Manual
check-ins…** remains an independent saved-observation workflow; choosing an OCR
target does not select its character/business, prefill an editor, save history,
change pins or schedule an action.

Local verification uses synthetic OCR through the real app and actual Linux
offscreen Qt controls/cards, including selection changes during a blocked OCR
call, Stop resets, stored-record independence and supported window sizes. It does
not establish Windows capture, real game-screen detection or recognition accuracy.

### Clear Live Business Readings

Use **Businesses → Clear live readings** to forget mistaken or outdated live
stock, supply and value observations for every business. The cards refresh
immediately to **Not tracked**, and later recommendation refreshes use the
cleared state. The selected **Business screen target** stays in place, so correct
it separately if the previous assignment was wrong.

Clearing is available while tracking, paused or stopped. It does not pause
capture: a fresh OCR batch can fill the cards again. Any business OCR batch
already in flight is retired, including when no previous readings are visible.
The app, parser and optimizer forget their live observations together; a
recommendation already returned to a caller is a snapshot and is not rewritten.

This is an in-memory reset. It leaves saved manual check-ins, pins, business
snapshots, settings, session statistics, goals, cooldowns and scheduled actions
alone. **Reset Session** still resets session statistics rather than these live
readings. General activity suggestions can remain after clearing; this control
removes the business observations that drive stock/supply advice.

Local tests exercise the real capture loop with synthetic OCR, blocked-batch
and recommendation races, and actual Linux offscreen Qt controls over disposable
SQLite records. Native Windows/game capture and OCR accuracy are unverified.

### Enter a Live Business Reading

On a business card, choose **Enter live reading…** when OCR is unavailable or you
want to correct its observation. The editor is fixed to that card's business and
starts with blank stock, supplies and value fields every time. Enter at least one
number and choose **Apply**. Percentages accept 0–100; dollar values accept whole
numbers from 0 to 9,223,372,036,854,775,807, without a currency symbol or commas.
Supplies are **Not applicable** for businesses without supply-based production
in the existing catalog.

Applying replaces that business's entire live observation. A blank field becomes
**Unknown**, even if an older reading had a value. Zero is an observed number:
`$0` is never replaced by an estimated sale value. A missing value can still be
estimated from known stock and is marked with `~`. The card identifies a
**Manual entry**. Invalid entries stay open for correction; closing an edited
draft asks whether to discard it.

The cards and QuickStats distinguish unknown stock from empty stock, and
unavailable production time from full stock. Selling suggestions need known
stock and positive observed or estimated value. Resupply suggestions need known
stock and supplies on a supply-based business. Partial observations remain
visible without inventing the missing inputs for advice. Estimates continue to
use the app's existing catalog rates; they do not verify the current game state.

This action replaces live working data only. It keeps the OCR target, other
businesses, session totals, reminders and saved history intact. Business OCR
already in flight is retired; a fresh OCR batch can replace the manual entry.
Capture continues while the editor is open. Use **Manual check-ins…** separately
when you want to save an observation for a character. The live editor does not
prefill from or write to that history.

Local validation covers the actual app and Linux offscreen Qt editor/cards with
disposable SQLite records, partial and zero-valued observations, draft/error
recovery, and delayed versus fresh OCR. Native Windows/game capture and OCR
accuracy remain unverified.

### Manual Business Check-ins

Open **Businesses → Manual check-ins…** to keep your own observations for a saved
character. Choose a character, select a business, then **Record check-in…**.
Enter any stock percentage, supply percentage, observed dollar value, or personal
note you want to record. The editor starts with blank fields and keeps its chosen
character and business even if you change the board or Settings while it is open.

- Stock and supply accept whole percentages from 0 to 100. Observed value accepts
  whole dollars from 0 to 9,223,372,036,854,775,807. Type digits without commas,
  currency symbols, signs, decimal points or exponent notation
- Blank measurements mean **unknown**; a typed zero is a known zero. Each check-in
  is a complete independent observation, so omitted fields do not carry values
  forward from an older entry. A note-only check-in is allowed
- Notes allow up to 2,000 Unicode code points, including newlines and tabs. Names
  and notes display literally. At least one measurement or a nonblank note is
  required; invalid input or a failed Save keeps your draft
- Save appends a new entry with a **Recorded at … UTC** timestamp. The latest board
  and history follow save IDs, so equal timestamps or a backward clock do not
  make an older entry replace a newer one
- Select a business to browse earlier check-ins, 25 at a time, and inspect the
  full selected note. Refresh reads current saved data; closing and reopening the
  board retains the records
- Use the history's **Note contains** and optional **From UTC / Until UTC**
  controls to find older observations, then **Apply** to load matching pages
- Choose **Pin selected business** to keep frequently used businesses first for
  this saved character. Choose **Unpin selected business** to remove that
  preference. Pins survive closing the board and restarting the app
- Export the displayed latest board or history page as JSON. These are separate
  accepted observations; an export retains the chosen snapshot even if data or
  selection changes while the file chooser is open

#### Copy live values into a check-in draft

In a check-in editor opened from **Businesses**, choose **Preview live values…**
to inspect the last tracked reading for that editor's fixed business. Review its
measurements, source and update time, then choose **Use these values**. This
replaces all three draft measurements: unknown values clear their fields, a known
zero stays zero, and supplies are **N/A** for businesses without a supply meter.
Estimated optimizer values are never copied. Your personal note stays exactly
as you wrote it.

Live readings are **not tagged to a saved character**. Confirm they belong to the
character and business shown in the preview. The preview identifies manual live
entry, OCR text matching or a user-selected OCR target when that metadata exists;
otherwise the source is not recorded. A naive live-update timestamp is local time,
while an aware timestamp retains its offset. Missing time is shown as unavailable.

Opening the preview does not change the draft. **Cancel**, Escape or closing the
preview leaves every draft field untouched. The preview keeps the exact snapshot
you reviewed if live readings, board selection or tracking change while it is
open. Use copies that snapshot once; open another preview explicitly to read newer
values. A missing or invalid reading keeps your draft available for manual entry.

**Save remains separate.** It records your reviewed draft for the editor's
original saved character/business using the existing new UTC save timestamp.
The preview's live source, update time and capture time are not stored as check-in
provenance. Copying does not start capture, record a sale or alter live values,
recommendations, settings or accounting.

Local tests exercise detached snapshots and locking, actual Linux offscreen Qt
preview controls, and synthetic OCR/manual live values through MainWindow →
check-in draft → explicit Save → reopen/export. They cover preserved personal
notes, changed live readings, fixed targets, errors/retry and modal reentry.
Windows, real gameplay/OCR accuracy and native file chooser behavior remain
unverified.

#### Review several live check-ins together

In **Businesses → Manual check-ins…**, choose the saved character, then
**Review live check-ins…**. The app captures all available catalog readings once
under its data lock. The review shows the fixed saved character, observed values,
reading sources, original update times and one common UTC capture time. It also
shows how many of the eleven catalog businesses have readings. Missing readings
are omitted; an invalid stored reading stops the capture without presenting a
partial set. With no readings, the review opens an explanatory empty state.

Nothing is selected initially. Select individual businesses or choose **Select
all**, and use **Clear selection** to remove the selection. An optional shared
personal note is copied unchanged to every selected check-in, including its
Unicode, nonbreaking spaces, newlines and tabs. Choose **Save selected check-ins
(N)** to record that reviewed selection together. The numeric fields are read-only
here; use **Record check-in…** when you want to enter or correct individual values.

Zero stays a known zero. Unknown or inapplicable measurements stay blank in saved
data, and no optimizer estimate or previous check-in fills a missing field. Live
readings are not character-tagged and have no expiry guarantee: verify that each
selected observation belongs to the saved character shown in this review.
Tracking, Settings, live-cache and board changes do not replace the open capture
or its saved-character target. Close it and explicitly open another review for
newer readings. Existing single-business drafts remain independent.

The selected rows append in one SQLite transaction with one new UTC Save time.
Every draft is validated before storage access, and every inserted row is checked
before commit. A known transaction-body failure rolls back the whole attempt and
keeps the selection and note for an explicit retry. If commit/close throws or a
returned result cannot be verified, the outcome is **uncertain**: the rows may
already be saved. The review keeps its values visible but disables editing and
Save. Inspect saved history before closing it and starting another attempt.
Successful Save cannot be repeated through the same review, even if the board
then fails to refresh.

The existing saved schema is unchanged. Source, live-update time and capture time
remain preview information; they are not added to notes or persisted as provenance.
History, comparison and JSON export use the same independent check-in records.
This workflow does not change live observations, pins, active characters, capture
settings, recommendations or accounting. It has no durable request identity, so
it does not promise exactly-once recovery after an abrupt exit or ambiguous save.

Local tests cover actual SQLite rollback and post-commit failure, one-lock raw
captures, Linux offscreen Qt review/selection/discard controls, and the real
MainWindow → batch Save → history/reopen/export path. They verify fixed ownership
through board and tracking changes and keep ordinary drafts untouched. Native
Windows, game capture/OCR accuracy and native file choosers remain unverified.

#### Filter recorded check-in history

Choose a saved character and business, enter a literal phrase in **Note contains**,
optionally enable either recorded-date bound, then choose **Apply**. The history
shows the matching total and up to 25 rows per page, newest save ID first. Select
a row to read its full personal note. **Clear** restores the unfiltered first
page for that character/business.

Search accepts up to 200 Unicode code points in a single line. ASCII letters
ignore case; other Unicode letters match exactly. `%`, `_` and backslashes are
literal characters, not wildcards. An empty phrase means no note filter; actual
spaces in a phrase are preserved. Each enabled date includes the entire chosen
UTC day. The date refers to when Save recorded the check-in, not a gameplay time;
stored offsets are normalized consistently with the displayed UTC timestamp.
A clock rollback can therefore put a newer save ID on an earlier recorded date.

Editing a filter clears the displayed history, selected note and history-export
action until **Apply**. The latest board and an open recording draft remain
available. Invalid input keeps the typed controls for correction. Paging and
Refresh use the accepted filters; Refresh does not silently apply unfinished
filter edits. Changing the selected character or business resets the filters.
Reordering pins or refreshing the same selection retains accepted filters.

Filtered JSON exports include the accepted phrase, UTC bounds and matching
policy with that exact history page. A file chooser captures the page before it
opens, so changing filters, selection or data while it is open does not rewrite
the chosen export. The existing unfiltered JSON shape remains compatible.
Matching counts cover all qualifying check-ins for the selected business, while
the export contains only the displayed page. Separate page reads are fresh
observations and do not form a multi-page database snapshot.

Filtering may scan that business's saved rows. The source fields used by active
filters are validated before matching; invalid saved note/date data causes an
error instead of silently disappearing as a nonmatch. Other fields retain the
existing displayed-page validation scope. A failed read is shown as unavailable,
not as zero matches. Filtering does not change saved observations, pins, live
business values or accounting.

Local tests cover real SQLite filtering and captured exports, plus the actual
MainWindow → record → filter → page → export flow with Linux offscreen Qt.
Native file choosers are substituted; Windows, gameplay and OCR are unverified.

Check-ins belong to saved characters. Choose **Add saved character…**, enter a
name, and choose **Create and select** to get started on an empty database without
running capture or OCR. You can immediately record a check-in, reopen it later,
and export its board or history. The new character also appears in History's
character filter, with no tracking sessions until you actually start tracking.

Names use 1–50 Unicode characters after surrounding spaces are removed. Case,
internal spaces and Unicode form are preserved; line breaks and control characters
are rejected. Exactly one existing character with that exact name is reused,
without changing its stored fields. Several legacy rows with the same name are
not merged or chosen automatically: select the intended ID in the board, or use
a different name. The creator supports up to 1,000 saved characters and can still
reuse an existing name at that limit.

Creation adds an inactive saved character and selects its exact committed ID in
this board. It does not change active-character flags, Settings, a running
session, live business observations or the character used for the next tracking
Start. That tracking name remains configured separately in Settings. Opening the
board alone creates no character, pin preference or check-in. When no current or
unique active character can be selected unambiguously, choose one explicitly;
duplicate names show their IDs.

The character editor is modeless, retains an invalid or failed-save draft, and
asks before discarding it. An already-open check-in editor keeps its original
character/business even when you create or select another character. A committed
creation is acknowledged before the board refreshes; a failed refresh cannot
turn it into a second creation. Refresh retries the exact saved ID, while choosing
another character explicitly replaces that pending selection.

Pins make an everyday manual shortlist without hiding other businesses or saved
history. Pinned catalog businesses appear first in catalog order, followed by
pinned historical IDs and then the remaining businesses. A star marks a pinned
row, and the selected business stays selected when its position changes. With no
pins, the normal catalog order is retained. Personal pins do not declare game
ownership, change Settings or active-character flags, or drive live OCR,
production estimates or recommendations. An open check-in draft keeps its
original character/business while you change the board or its pins.

The additive `manual_business_pins` table stores each character/business pair
once. Each explicit action sets one desired preference, so concurrent changes to
different businesses are retained; repeated Pin or Unpin is idempotent. Opposite
actions on the same business follow transaction order. Removing a pin removes
only that preference, and the business can be pinned again. Saved observations,
their history and existing JSON export contents are unchanged. Exports retain
all accepted observations, including unpinned businesses; pins affect display
order, not the exported data selection.

Pin reads and writes are bounded to 256 preferences per character and require an
existing saved owner. New pins use the current catalog. Valid historical pin IDs
remain visible and can be unpinned even when no longer in the catalog; they do
not become recordable businesses. If pin data cannot be read, the board still
shows valid observations/history in default order and disables pin changes until
a successful Refresh. A failed write preserves the previous preferences. A
committed change remains acknowledged even if its following display refresh
fails. These are local personal preferences, with no claim of protection against
arbitrary external database modification or character-ID reuse.

The manual creator and the existing tracking creator acquire SQLite's writer
lock before checking a name. This serializes these two app creation paths, at the
cost of briefly locking even an existing-name lookup. It does not add a database
unique constraint, repair old duplicate rows, or coordinate arbitrary external
database writers. Failed saves roll back; the creator provides no character
rename, deletion or merging. Linux offscreen tests exercise the actual MainWindow,
dialogs, persistence and export; native Windows, gameplay and OCR remain untested.

These are your saved observations, with the recorded time showing when Save ran.
They do not advance stock over time, estimate sale prices or production, verify
current game state, or drive the app's recommendations, live OCR cards, goals or
earnings. The business selector uses the existing catalogue's names; it does not
certify current game mechanics or which properties a character owns. Legacy
business snapshots remain separate from these manual entries.

The additive `manual_business_checkins` table and its character/business/save-ID
index are created alongside existing SQLite tables. This workflow appends records
and provides no edit, delete, backdating or import operation. A character must
still exist when an entry is inserted. Failed commits are not reported as saves;
if a successful save is followed by a failed display refresh, its success remains
visible and Refresh retries only the read. Invalid stored entries are reported as
unavailable instead of silently substituted with earlier values or zeros.

Reads are bounded to 1,000 character choices, 256 latest business groups, and
history pages of 1–100 rows with offsets up to 1,000,000. Board and history queries
capture their context and rows within one SQL observation; the two views are not
a shared multi-query transaction. JSON exports are limited to 8 MiB UTF-8 and
write through same-directory staging before replacing the destination. A failed
write preserves a previous export; this per-file replacement is not a power-loss
durability guarantee. Exports include the selected character and your personal
notes, so share them accordingly.

Local tests exercise temporary real SQLite databases, validation and rollback,
character isolation, insertion ordering, concurrent readers/writers, and the
actual MainWindow → Businesses → editor → reopen/history/export path. Native tests:
`GTA_RUN_QT_TESTS=1 QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_business_checkins_qt.py tests/test_business_checkins_app_qt.py`.
Ordinary pytest skips these opt-in UI cases and does not require Qt. Native test
mode keeps one QApplication alive for the entire pytest session, while each test
cleans up its own widgets. Native Linux offscreen rendering is
tested with substituted file choosers and synthetic capture boundaries; Windows,
OCR accuracy and live game behavior remain unverified. No hosted tests are used.

#### Compare two recorded check-ins

In **Businesses → Manual check-ins…**, select a history row and choose
**Use as baseline**. Locate another saved observation for the same character and
business, then choose **Compare with baseline**. The read-only comparison shows
both recorded UTC times, full personal notes, and stock, supply and observed-value
differences. It works with capture stopped.

Baseline A stays selected across history pages, filter edits, Apply, Clear and
Refresh for the same character/business. It may therefore be outside the displayed
page or filter. Changing character or business, choosing **Clear baseline**, or
closing the board clears it. Unapplied filters have no current comparison row;
Apply before comparing again. The same check-in cannot occupy both roles.

**Compare** freshly reads both selected records and their character together.
The dialog then keeps that detached pair: later saves, navigation or database
changes do not rewrite it. Close the comparison and choose another pair to read
again. If a selected record is missing or no longer belongs to that character
and business, comparison is unavailable; a missing baseline must be selected
again. A storage failure leaves the selection available for retry.

Every difference is **comparison B minus baseline A**, preserving your chosen
order even when B has an earlier save ID or recorded time. Stock and supplies
use **percentage points**: 30% minus 10% is +20 points. A missing measurement on
either side leaves its difference unavailable; recorded zero remains zero.
Dollar values and their signed differences retain exact whole integers. These
are changes between your observations, not production, elapsed gameplay, sale
proceeds, profit, rates or evidence that one result is better. The recorded time
shows when Save ran and may move backward with the clock.

**Export comparison as JSON…** saves that displayed pair, character context,
full notes, units and differences without rereading storage. It has its own
versioned report kind; existing board/page exports keep their format. Output is
limited to 256 KiB UTF-8 and uses atomic per-file replacement. Cancel writes
nothing; a failed write keeps an earlier file and can be retried. This does not
provide multi-file transactions, concurrent-writer coordination or power-loss
durability. A file chooser keeps its original comparison, and the board cannot
close while that export is active.

Local tests cover actual SQLite ownership, null/zero and maximum integer values,
clock ordering, corrupt selected data, concurrent writes and captured exports.
The MainWindow record → page/filter → compare → export path is exercised with
Linux offscreen Qt, including literal notes and small-window controls. Native
file choosers are substituted; Windows, OCR and live gameplay remain unverified.

#### View recorded history trend

In **Businesses → Manual check-ins…**, choose a saved character and business,
apply any history note/UTC-date filters, then choose **View history trend…**.
The modeless view captures **all matching saved check-ins**, including records
outside the displayed 25-row page. It does not require a selected history row
or comparison baseline. Pending filter edits must be applied first.

The view contains:

- Recorded stock and supply percentages, plus a separate observed-value chart
- Known/unknown counts for each measurement and the first/last selected values
- Exact last-minus-first changes when there are at least two records and both
  endpoint fields are known; stock/supply changes are percentage points
- A complete table with sequence number, exact check-in ID, full recorded UTC
  time and measurements, plus the selected record's complete personal note

The horizontal chart axis is **selected check-in sequence, oldest save first**.
It counts the selected observations from 1, rather than using elapsed time or
raw database IDs. Insertion IDs determine order even if recorded clocks are
identical or move backward. The table retains each actual UTC timestamp,
including subseconds. These are save times, not gameplay timestamps.

Every selected row retains its place. Unknown measurements create chart gaps;
lines only join adjacent known observations and isolated known points remain
visible. Zero remains a known zero. Endpoint summaries use the first and last
selected records even when their fields are unknown: they do not search inward
for another measurement. A single record has no change, and note-only records
still count. No value is carried forward, smoothed, extrapolated or converted
into a production rate, sale proceeds or profit.

Observed values and their changes stay exact Python integers in the table,
summary and JSON. If any known value cannot be represented exactly by the
chart's floating-point coordinates, the entire value chart is unavailable with
an explanation; its exact observations remain available. Unsafe points are not
silently rounded or dropped. Stock/supply charts remain available independently.
If the plotting dependency is unavailable, the summary, table, full notes and
export still work.

The snapshot is limited to **1,000 matching records**. A larger selection is
refused with a request to narrow the applied filters; it does not show a partial
trend or a partial summary. An empty selection is a valid empty view. The
existing literal-note matching, inclusive UTC-date rules and corrupt-data
refusals apply. Historical valid business identifiers remain inspectable.

An open view keeps its captured character, business, filters and records through
later saves, parent navigation or database changes. Repeated opens raise that
same view. Close and reopen it to capture another selection. **Export trend as
JSON…** saves this exact snapshot without rereading storage: all selected rows,
raw integers/nulls/full notes, coverage, endpoint changes, plot availability,
filter policies, cap and capture time. The format is version 1,
`manual_business_checkin_trend`; it is separate from a paginated history export.
The existing 8 MiB UTF-8 export limit and per-file atomic replacement apply;
an oversized export is refused rather than truncated. A completed export does
not promise crash durability or a transaction with other files.

This is read-only inspection of saved manual observations. It does not start
capture, consult live OCR readings, change the saved character, modify pins,
update recommendations or write accounting/check-in records. Local validation
uses temporary SQLite databases, real Linux offscreen Qt/pyqtgraph paths and the
actual MainWindow workflow, including filtered selections larger than one page,
clock rollback, gaps, integer precision, fixed exports and small-window layouts.
Native Windows, gameplay/OCR and native file-chooser behavior remain unverified.

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

### Diagnose one saved screenshot

Use an existing local PNG or JPEG to inspect the current mission detector without
starting capture or a tracking session:

```bash
python test_capture.py --image screenshot.png
# Explicit optional diagnostic backend; Tesseract and its English data must already be installed
python test_capture.py --image screenshot.jpg --backend tesseract
```

Windows OCR is the default. An unavailable backend or failed recognition returns
a nonzero exit code; the tool never chooses a replacement backend automatically.
Tesseract is only a diagnostic option for this command, not an application OCR
fallback or a test of Windows OCR.

The command prints one JSON report containing the input hash and dimensions,
default production crop coordinates, actual requested preprocessing and OCR text,
and a fresh detector's state/mission candidate. Sources skipped by the detector
are identified separately from successful empty text. Native Windows OCR does
not provide confidence, so its OCR confidence is `null`; the detector's heuristic
score is a different measure and is not an accuracy estimate. Exit zero means
the diagnostic ran, including when its candidate is unknown or incorrect.

This reads one stored canvas with no rotation, crop correction, custom regions
or loaded templates. Inputs must be single-frame PNG/JPEG files of at most
32 MiB, 100–8192 pixels on each axis and at most 16,777,216 pixels in total;
nontrivial orientation metadata is unsupported. It does not load settings or
write images, history, cooldowns or financial records. The report is isolated
detector evidence, not proof that the application would start or complete an
activity. Still images cannot validate episode continuity, live capture, native
Windows behavior, or general gameplay accuracy.

[Five guide-image controls](validation/screenshot-diagnostic-evaluation.json)
record exact input hashes and the new command's parity with the existing
diagnostic adapter across all 30 requested crop readings. At that baseline,
Sightseer remained a miss and Headhunter supplied VIP-work category evidence
without a mission name. These are public-guide stills, not YouTube frames or a
continuous session. The image files remain outside this repository.

The no-argument live self-check remains available. Missing required captures,
unavailable/failed OCR and incomplete five-cycle checks now fail explicitly;
successful native OCR with unknown confidence is supported, and captures are
closed on return and error paths.

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
- `manual_business_checkins` - Character-scoped manual observations and personal notes
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
retry loop. Batched HUD crops share one grab and wait only once between batches. These are
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
| bottom_objective | Restricted named-objective evidence | Bottom-center |
| result_header | Qualified heist-result evidence only | Upper-center |
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

### Live business stock and supply ratios

Business OCR treats `Stock: 5/10` as 50% and `Supplies: 3/4` as 75%.
Integer ratios require a positive denominator and a numerator between zero and
that denominator; fractional percentages round down (`1/3` becomes 33%). A slash
alone is not a percent sign. Ordinary text such as `Stock: 50%` still works.
The resulting live values reach the optimizer and the Businesses tab without
calling an unsupported stock-update method on the explicit-action scheduler.

Tests exercise the real capture loop with synthetic OCR and the actual business
cards in Linux offscreen Qt. The live observations leave recorded manual check-ins
and database history untouched. Business-name recognition, absent-field defaults
and value estimates retain their existing behavior. Windows OCR, real screenshots
and in-game recognition accuracy remain separate validation work.

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

### Tracking detected heist phases

The existing `HEIST_PREP` and `HEIST_FINALE` detector states now start an activity
when no mission is being tracked, using their corresponding activity types. They
capture the same start time and balance as ordinary missions and reach the same
success/failure persistence path. Generic active missions retain their existing
text-based type inference, and later noisy/repeated states do not replace an
already-active activity or reset its baseline.

Tests link the actual OCR-text state classifier to real trackers and SQLite using
synthetic recognized text, including both outcomes and intermediate state changes.
This does not change OCR keywords, detection confidence, automatic phase-boundary
recognition or native gameplay behavior; no screenshot/Windows OCR accuracy claim.

Detected heist prep/finale states also use the configured active capture rate and
the existing HUD timer OCR path when a timer image is available. They share the
same active-rate validation/fallback as other missions. Local tests run the real
capture loop and timer parser with synthetic frames, covering normal missions,
sells, heists, missing timer captures and existing idle/business rates; this is
policy/processing coverage rather than a native FPS or OCR-accuracy benchmark.

### Activity timestamp compatibility

Activity duration checks, completion and cancellation now obtain the current time
using the start timestamp's timezone convention. Default activities keep naive
local timestamps; explicitly timezone-aware activities retain their timezone.
Canceling an ordinary activity, including replacing it with a new activity, no
longer mixes a naive start with an aware UTC end and fails when its duration is read.
Cancellation still leaves completed history untouched and retains its existing
failure/notes behavior; serialized timestamp fields keep their existing format.

Local tests cover the default tracker lifecycle, UTC and fixed positive/negative
offsets, completion, cancellation, replacement and JSON timestamps. This is clock
compatibility, not monotonic elapsed-time measurement, normalization of older mixed
timestamps, protection from system-clock changes or native Windows validation.

### Recent game-state transitions

Recent transition queries iterate over the retained deque instead of attempting
unsupported slicing. They return the requested newest transitions first, preserve
the existing default of ten and return an empty list for nonpositive counts.
Queries do not change the current state or retained history; the existing history
limit still applies. Local tests exercise actual transitions/listeners, ordering,
empty requests and retention. Returned transition objects remain shared, and this
does not add concurrent-access guarantees or native UI/gameplay validation.

Repeated detections of the current state now leave its original context and entry
time intact. They return `False` from `transition_to`, add no history row and send
no state-change notification. A different detected state still starts a new timer
and follows the existing transition/warning rules. Tests cover every state, a
listener repeating the current state, and repeated capture cycles through the
actual app method with synthetic frames; no native OCR or performance claim.

### Mission completion listeners

The app clears the completed mission's start time, opening balance and name before
calling `on_mission_complete` listeners. The completed `Activity` still carries its
name, type and payout, and tracking/session/database updates happen first. A listener
reprocessing the old result therefore cannot count it again. A listener may also
start or complete the next activity without the outer notification clearing that
new mission's state.

Each completion uses a snapshot of its listeners. Registration changes take effect
for later completions, including a distinct nested completion, while the current
event retains its captured activity and listener list. Listener exceptions remain
logged and do not skip later listeners. Local tests link callbacks to the actual
trackers and SQLite, including repeated results, all active mission types and nested
completions. This does not add general thread safety, change payout/detection policy,
or validate native OCR/UI behavior.

### Estimates after clock rollback

Cooldowns, passive-income accumulation and nightclub popularity decay now treat
time before their recorded start/update as zero elapsed time. A future saved
timestamp or backward clock adjustment cannot create negative income, increase
popularity above its recorded value, or extend a cooldown past its original
duration. The stored observations and timestamps remain intact; ordinary estimates
resume when the wall clock reaches those timestamps.

Existing rates, capacities, decay rules and legacy-naive-as-UTC interpretation
remain unchanged. This bounds these elapsed-time estimates, without replacing
persisted wall-clock time with a monotonic clock or correcting old records. Local
tests cover UTC/offset/legacy timestamps, real JSON reloads and normal later accrual
and expiry. Native game timing and the accuracy of game-economy constants are not
validated by this change.

### Consistent passive-income predictions

Each prediction now samples its estimated value once and derives the percentage,
full flag and remaining time from that amount. An estimated full safe or warehouse
therefore shows 100% and Full together. Both the detailed and compact widgets use
these prediction rows, and the detailed total sums the displayed amounts instead
of sampling them again.

Reading predictions does not update saved balances, timestamps or the state
properties describing the last observation. Existing rate/capacity tables,
recommendation thresholds and time model remain unchanged. Local tests cover
partial/full estimates, inactive sources, clock rollback, ETA formatting and the
actual widget update methods using stand-in Qt labels. Native Qt rendering and
in-game forecast accuracy remain unverified.

### Session displays after reset

The dashboard, overlay and session panel refresh their hourly-rate labels even
when the new rate is zero or not yet available. Reset Session therefore clears the
previous rate immediately; the overlay and session panel retain their existing
60-second warm-up. Both Activities Completed counters use the current session's
full count, including failed completions, rather than the ten-row recent list.

Unavailable session-panel values clear their old text and status color. Valid
zero balances still display as zero. This changes presentation only: reset keeps
the existing recent activity history, average calculation and analytics selection.
Local regressions run the actual update methods against real app accounting,
session resets and disposable SQLite, substituting only the Qt display objects.
These dashboard, overlay and session-panel label tests do not validate native
rendering or in-game capture.

Session earnings charts also follow the current session object. A reset or new
run clears the previous points even when initiated outside the session panel.
Elapsed chart time comes from session duration, so a chart opened later uses the
correct timeline and a completed session's endpoint stops advancing. Empty or
unavailable activity breakdowns clear the previous bars and labels. Existing
sampling, earnings and retained-history analytics calculations are unchanged.

The regular Python suite uses actual chart methods with stand-in plots. With the
existing PyQt6/pyqtgraph dependencies installed, opt-in native chart smoke tests
run with `GTA_RUN_QT_TESTS=1 QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_charts_qt.py`
on a shell supporting inline environment variables. These tests exercise real
widgets without screen capture or model downloads; set the same environment
variables separately on other shells. They are skipped in the ordinary suite.
The offscreen Qt path is validated on Linux; Windows and in-game rendering remain
unverified.
