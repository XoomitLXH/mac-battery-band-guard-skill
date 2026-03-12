---
name: mac-battery-band-guard
description: Adaptive macOS battery-band monitoring for keeping a Mac between 40% and 80% with low-noise reminders. Use when creating, installing, debugging, or tuning a Mac battery monitor that warns near the 40% floor, warns at/above the 80% ceiling, and chooses its next check interval from recent charge/discharge behavior instead of polling at a fixed cadence.
---

# Mac Battery Band Guard

Keep a Mac in the 40%-80% range without checking too often.

Use the bundled script to read battery state with `pmset`, persist a small history, estimate recent charge/discharge speed, choose the next sleep interval, and emit reminders only when a threshold transition matters. Reminder delivery supports local macOS notifications and optional Feishu push via OpenClaw.

## Workflow

1. Validate the current machine with a dry run.
2. Tune thresholds only if the user asks; the defaults are already sane.
3. Install the LaunchAgent for continuous background monitoring.
4. Check the persisted state or logs when reminders do not match expectations.

## Quick Start

Run a dry sample first:

```bash
python3 scripts/battery_guard.py once --print-only
```

Install the per-user LaunchAgent:

```bash
python3 scripts/battery_guard.py install-launch-agent
```

Install it with Feishu push to a direct chat/open_id:

```bash
python3 scripts/battery_guard.py install-launch-agent \
  --feishu-target ou_xxx
```

Inspect saved state later:

```bash
python3 scripts/battery_guard.py status
```

Remove the LaunchAgent:

```bash
python3 scripts/battery_guard.py uninstall-launch-agent
```

## Default Behavior

- Treat `40%` as the floor.
- Treat `45%` as the early “charge soon” warning band.
- Treat `80%` as the charging stop ceiling.
- Reset low-battery warnings after the machine climbs back above `50%`.
- Reset stop-charging warnings after the battery drops below `75%` on a later discharge cycle.
- Choose the next check interval between `5` and `180` minutes.

## Adaptive Interval Heuristic

Do not use a fixed polling schedule unless the user explicitly asks for one.

The script already does this:

- Keep recent battery samples in a small JSON state file.
- Estimate charge or discharge rate from adjacent samples in the same mode.
- Weight recent samples more heavily than old ones.
- Convert the estimated rate into an ETA to `40%` or `80%`.
- Sleep longer when the machine is far from the relevant threshold.
- Sleep much less when the machine is likely to hit a threshold soon.

This gives “usage-frequency-aware” behavior without reading app-level activity data. Heavy use naturally increases discharge slope, which shortens future checks; light use stretches them out.

## Notification Rules

Emit only meaningful reminders:

- On battery and `<=45%`: remind to charge soon once per discharge cycle.
- On battery and `<=40%`: escalate to a stronger charge-now reminder once per discharge cycle.
- While charging and `>=80%`: remind to unplug once per charge cycle.

Avoid repeat spam by relying on the persisted cycle counters and hysteresis resets.

Delivery options:

- Local macOS notification: enabled by default.
- Feishu push: enabled when `--feishu-target <open_id>` is provided.
- Local-only mode: keep defaults.
- Feishu-only mode: pass both `--feishu-target <open_id>` and `--disable-local-notify`.

## Script Reference

### `scripts/battery_guard.py once`

Sample once, update history, compute the next interval, optionally notify, and print a JSON snapshot.

Useful flags:

- `--print-only` — compute without sending any reminder
- `--disable-local-notify` — suppress local macOS notifications
- `--feishu-target <open_id>` — send reminder messages to a Feishu DM target through OpenClaw
- `--feishu-account <id>` — optional OpenClaw Feishu account id when multiple Feishu accounts exist
- `--lower <int>`
- `--soon <int>`
- `--upper <int>`
- `--min-interval <minutes>`
- `--max-interval <minutes>`
- `--state-file <path>`

### `scripts/battery_guard.py run`

Run forever with adaptive sleeps. This is the mode the LaunchAgent uses.

### `scripts/battery_guard.py install-launch-agent`

Write and load `~/Library/LaunchAgents/ai.openclaw.mac-battery-band-guard.plist` so the guard starts at login and stays alive.

### `scripts/battery_guard.py uninstall-launch-agent`

Unload and remove the LaunchAgent plist.

### `scripts/battery_guard.py status`

Print the persisted state JSON for debugging.

## Files Written by the Script

State directory:

```text
~/Library/Application Support/mac-battery-band-guard/
```

Important files:

- `state.json` — battery history, cycle counters, last notifications
- `guard.stdout.log` — LaunchAgent stdout
- `guard.stderr.log` — LaunchAgent stderr

## Tuning Guidance

Only change thresholds when the user asks.

Good reasons to tune:

- They want a narrower or wider battery band.
- They want earlier low-battery warnings.
- They want more or less aggressive monitoring.

When tuning, prefer changing just these values:

- `--lower`
- `--soon`
- `--upper`
- `--min-interval`
- `--max-interval`

Do not replace the adaptive loop with a tight fixed poll unless the user explicitly wants that tradeoff.
