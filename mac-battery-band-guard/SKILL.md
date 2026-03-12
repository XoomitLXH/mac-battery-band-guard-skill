---
name: mac-battery-band-guard
description: Adaptive macOS battery-band monitoring for keeping a Mac between healthier charge limits with low-noise reminders, ETA prediction, anomaly detection, summaries, and profile-based behavior. Use when creating, installing, debugging, or tuning a Mac battery monitor that should warn near a floor, warn at/above a charging ceiling, adapt check intervals from recent battery behavior, send local/Feishu reminders, support quiet hours, and generate battery-health suggestions.
---

# Mac Battery Band Guard

Keep a Mac in a healthier battery band without noisy fixed-interval polling.

Use the bundled script to read battery state with `pmset`, store lightweight history, estimate current charge or discharge speed, adapt the next sleep interval, detect unusual drain, send reminders, and generate summaries plus habit-based suggestions.

## Core capabilities

- Adaptive check intervals from real battery behavior
- ETA-aware low-battery and stop-charging reminders
- More natural reminder wording
- Unusual drain detection
- Daily and weekly summaries
- Profiles: `balanced`, `work`, `outing`, `night`
- Quiet hours
- Temporary charging-ceiling override
- Habit-based suggestions from recent history
- Local macOS notifications and optional Feishu push through OpenClaw

## Quick start

Run a dry sample:

```bash
python3 scripts/battery_guard.py once --print-only
```

Install the background LaunchAgent:

```bash
python3 scripts/battery_guard.py install-launch-agent
```

Install with Feishu push:

```bash
python3 scripts/battery_guard.py install-launch-agent \
  --feishu-target ou_xxx
```

Inspect current state:

```bash
python3 scripts/battery_guard.py status
```

## Profiles

Use profiles for different situations instead of hand-tuning every threshold.

- `balanced` — normal 40–80 behavior
- `work` — similar band, slightly earlier low warning and tighter cadence
- `outing` — travel-friendly profile with a higher ceiling
- `night` — normal band plus default quiet hours (`23:00-08:00`)

Persist a profile:

```bash
python3 scripts/battery_guard.py set-mode balanced
python3 scripts/battery_guard.py set-mode outing
python3 scripts/battery_guard.py set-mode night
```

## Temporary overrides

Temporarily raise or lower the charging ceiling for a limited time:

```bash
python3 scripts/battery_guard.py set-temp-upper 95 --hours 12
python3 scripts/battery_guard.py clear-temp-upper
```

Use this for travel days or one-off exceptions instead of permanently changing the main profile.

## Summaries and suggestions

Generate summaries:

```bash
python3 scripts/battery_guard.py summary --period day
python3 scripts/battery_guard.py summary --period week
```

Send a summary through the active reminder channels:

```bash
python3 scripts/battery_guard.py summary --period day --send --feishu-target ou_xxx
```

Generate habit-based recommendations:

```bash
python3 scripts/battery_guard.py suggest
```

## Important flags

- `--profile <balanced|work|outing|night>`
- `--lower <int>`
- `--soon <int>`
- `--upper <int>`
- `--quiet-hours <HH:MM-HH:MM>`
- `--daily-summary-hour <0-23>`
- `--weekly-summary-weekday <0-6>` (`0=Mon`, `6=Sun`)
- `--weekly-summary-hour <0-23>`
- `--disable-local-notify`
- `--feishu-target <open_id>`
- `--feishu-account <id>`
- `--print-only`
- `--state-file <path>`

## Files written by the script

State directory:

```text
~/Library/Application Support/mac-battery-band-guard/
```

Typical files:

- `state.json` — battery history, notifications, summaries, profile state, temporary overrides
- `guard.stdout.log`
- `guard.stderr.log`

## Guidance

Prefer profiles over over-customization.

Only tune raw thresholds when the user explicitly wants a different battery philosophy. For most cases:

- `balanced` for everyday use
- `outing` for travel or long unplugged sessions
- `night` when you want fewer late alerts

Do not replace the adaptive loop with a tight fixed poll unless the user explicitly asks for that tradeoff.
