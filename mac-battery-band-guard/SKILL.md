---
name: mac-battery-band-guard
description: Adaptive macOS battery-band monitoring for keeping a Mac between healthy charging limits with low-noise reminders, ETA prediction, anomaly detection, daily/weekly summaries, mode switching, quiet hours, temporary upper-limit overrides, and Feishu push delivery. Use when creating, installing, tuning, or debugging a Mac battery helper that should learn from recent charge/discharge behavior instead of polling at a fixed cadence.
---

# Mac Battery Band Guard

Keep a Mac in a healthier charging band without turning it into a noisy polling daemon.

Use the bundled script to:

- sample battery state via `pmset`
- persist a rolling history
- estimate current charge/discharge speed
- predict ETA to threshold crossings
- detect unusually fast battery drain
- send smarter reminders locally and/or through Feishu
- produce daily and weekly battery summaries
- switch between profiles like `default`, `work`, `travel`, and `night`
- apply temporary upper-limit overrides for travel or one-off heavy-use days

## Core idea

Do not run a dumb fixed-interval checker unless the user explicitly asks for one.

This skill should stay adaptive:

- long sleeps when far from important thresholds
- short sleeps when the current slope says the Mac is approaching 40% or 80% soon
- richer reminders when the battery is behaving abnormally
- summaries and habit suggestions built from persisted history

## Quick Start

Dry run one sample:

```bash
python3 scripts/battery_guard.py once --print-only
```

Install background monitoring:

```bash
python3 scripts/battery_guard.py install-launch-agent
```

Install with Feishu push:

```bash
python3 scripts/battery_guard.py install-launch-agent \
  --feishu-target ou_xxx
```

Show current state:

```bash
python3 scripts/battery_guard.py status
```

Show learned report / summary metrics:

```bash
python3 scripts/battery_guard.py report
```

## What the upgraded version supports

### Stage 1 behavior

- threshold reminders near the lower band and upper band
- ETA-based reminder text
- anomaly detection for unusually fast discharge
- daily summary notification
- weekly summary notification with habit suggestions

### Stage 2 behavior

- profile switching: `default`, `work`, `travel`, `night`, `auto`
- quiet hours, especially useful in `night` mode
- automatic day/night switching when `auto` is enabled
- temporary upper override for one-off travel / long unplugged sessions

### Stage 3 behavior

- lightweight habit learning from persisted history
- long-term suggestions in reports and weekly summaries
- profile-aware recommendations such as “switch to night mode” or “you often stay above the upper band too long”

## Reminder delivery

Supported channels:

- local macOS Notification Center
- optional Feishu push via OpenClaw

Quiet-hours behavior:

- critical alerts still surface
- lower-priority alerts can become Feishu-only during quiet hours

## Profiles

### `default`

Balanced 40–80 behavior.

### `work`

Similar charging band, slightly more workday-oriented summary timing.

### `travel`

Allows a higher ceiling by default so the machine can be taken out with more charge.

### `night`

Adds quiet-hours behavior and a slightly more conservative low-battery posture.

Switch profile:

```bash
python3 scripts/battery_guard.py set-profile night
python3 scripts/battery_guard.py set-profile auto
```

When `auto` is enabled, the script can use one profile for daytime and another for quiet hours:

```bash
python3 scripts/battery_guard.py set-auto-profiles --day-profile work --quiet-profile night
```

For travel days, enable a temporary travel override that auto-expires:

```bash
python3 scripts/battery_guard.py start-trip --hours 12 --upper 95 --set-profile-auto
python3 scripts/battery_guard.py end-trip
```

To test reminder delivery without waiting for a threshold:

```bash
python3 scripts/battery_guard.py test-alert --feishu-target ou_xxx
```

## Temporary upper override

Use this when the user wants one-off flexibility without permanently changing the profile.

Raise the upper limit temporarily:

```bash
python3 scripts/battery_guard.py set-temp-upper 95 --hours 12
```

Clear it:

```bash
python3 scripts/battery_guard.py clear-temp-upper
```

## Quiet hours

Set quiet hours with a simple `HH-HH` format:

```bash
python3 scripts/battery_guard.py set-quiet-hours 23-08
```

## Important commands

### `once`

Sample battery, update history, compute alerts, optionally notify, and print a JSON snapshot.

Useful flags:

- `--print-only`
- `--disable-local-notify`
- `--feishu-target <open_id>`
- `--feishu-account <id>`
- `--profile <default|work|travel|night>`
- `--quiet-hours <HH-HH>`
- `--summary-hour <0-23>`
- `--weekly-summary-weekday <0-6>`

### `run`

Run continuously with adaptive sleep intervals. This is what the LaunchAgent uses.

### `install-launch-agent`

Install the per-user LaunchAgent and seed initial runtime settings.

### `report`

Print a structured summary including:

- today’s tracked stats
- last 7 days summary stats
- current profile
- active overrides
- learned suggestions

## Files written by the script

State directory:

```text
~/Library/Application Support/mac-battery-band-guard/
```

Important files:

- `state.json`
- `guard.stdout.log`
- `guard.stderr.log`

## Tuning guidance

Prefer profiles and temporary overrides before changing raw thresholds.

Good reasons to tune raw thresholds directly:

- the user has a very different preferred battery band
- they want much earlier low-battery warnings
- they want much tighter or looser adaptive intervals

Most users should first try:

- `set-profile travel`
- `set-profile night`
- `set-temp-upper 95 --hours 12`
- `set-quiet-hours 23-08`

Keep the system adaptive unless the user explicitly asks for fixed polling.
