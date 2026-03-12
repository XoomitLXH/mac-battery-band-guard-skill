# mac-battery-band-guard-skill

An adaptive macOS battery monitoring skill for keeping your Mac in a healthier charging band without noisy fixed-interval polling.

The project started as a 40%–80% battery reminder, then grew into a fuller battery assistant with:

- ETA prediction
- unusual drain detection
- more natural reminder wording
- daily and weekly summaries
- profile-based behavior
- quiet hours
- temporary travel overrides
- habit-based suggestions
- local notifications and Feishu push

## What it does

### Stage 1 improvements

- reminds you to **charge soon** near the lower band
- reminds you to **charge now** below the floor
- reminds you to **stop charging** at or above the upper ceiling
- predicts how long it may take to hit the next threshold
- detects **unusual battery drain** compared to recent history
- uses more useful, human-readable reminder text
- can produce **daily** and **weekly** battery summaries

### Stage 2 improvements

- supports profiles:
  - `balanced`
  - `work`
  - `outing`
  - `night`
- supports **quiet hours**
- supports a **temporary upper limit override** for travel or special days

### Stage 3 improvements

- learns from recent plug-in / unplug patterns
- generates habit-based suggestions
- offers lightweight long-term battery behavior guidance

## Why it is adaptive instead of frequent polling

The monitor keeps a local history of battery samples and estimates current charge or discharge speed from real use.

It then uses that estimate to predict ETA to the next meaningful threshold:

- ETA to the floor while discharging
- ETA to the ceiling while charging

That lets it adjust the next check interval automatically:

- far from thresholds → check less often
- near thresholds → check more often
- heavy use → shorter intervals
- light use → longer intervals

So it follows your actual battery behavior instead of running a noisy, rigid polling loop.

## Reminder channels

### 1. Local macOS notifications

By default, reminders are shown through Notification Center via `osascript`.

### 2. Feishu push notifications

If you pass a Feishu target, the same reminder can also be sent through OpenClaw to your Feishu chat.

Example:

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py install-launch-agent \
  --feishu-target ou_xxx
```

Feishu-only mode:

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py install-launch-agent \
  --feishu-target ou_xxx \
  --disable-local-notify
```

## Profiles

Profiles let the monitor behave differently without forcing you to manually tune every threshold.

- `balanced` — default everyday behavior
- `work` — slightly tighter monitoring and earlier low warning
- `outing` — more travel-friendly ceiling
- `night` — default quiet-hours behavior

Persist a profile:

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py set-mode balanced
python3 mac-battery-band-guard/scripts/battery_guard.py set-mode outing
python3 mac-battery-band-guard/scripts/battery_guard.py set-mode night
```

## Temporary charging ceiling override

Useful for travel days or special cases:

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py set-temp-upper 95 --hours 12
python3 mac-battery-band-guard/scripts/battery_guard.py clear-temp-upper
```

## Summaries and suggestions

### Daily / weekly summaries

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py summary --period day
python3 mac-battery-band-guard/scripts/battery_guard.py summary --period week
```

Send a summary through the configured reminder channels:

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py summary --period day --send --feishu-target ou_xxx
```

### Habit-based suggestions

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py suggest
```

Typical suggestion categories:

- you usually plug in too late
- you usually unplug too high
- discharge speed has been unusually high
- charging has recently looked slower than expected

## Main script usage

### Dry run

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py once --print-only
```

### Install background monitor

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py install-launch-agent
```

### Install with Feishu push

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py install-launch-agent \
  --feishu-target ou_xxx
```

### Show saved state

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py status
```

### Remove background monitor

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py uninstall-launch-agent
```

## Important flags

- `--profile <balanced|work|outing|night>`
- `--lower <int>` — lower battery floor
- `--soon <int>` — early low-battery reminder threshold
- `--upper <int>` — upper stop-charging threshold
- `--quiet-hours <HH:MM-HH:MM>`
- `--daily-summary-hour <0-23>`
- `--weekly-summary-weekday <0-6>`
- `--weekly-summary-hour <0-23>`
- `--min-interval <minutes>` — shortest adaptive interval
- `--max-interval <minutes>` — longest adaptive interval
- `--print-only` — compute but send no reminders
- `--disable-local-notify` — disable macOS notifications
- `--feishu-target <open_id>` — push reminders to a Feishu DM target
- `--feishu-account <id>` — optional OpenClaw Feishu account id if multiple accounts exist
- `--state-file <path>` — custom state file location

## Files written locally

By default the script stores state under:

```text
~/Library/Application Support/mac-battery-band-guard/
```

Typical files:

- `state.json`
- `guard.stdout.log`
- `guard.stderr.log`

## Repository layout

```text
mac-battery-band-guard-skill/
├── README.md
├── dist/
│   └── mac-battery-band-guard.skill
└── mac-battery-band-guard/
    ├── SKILL.md
    └── scripts/
        └── battery_guard.py
```

## OpenClaw integration

This repository includes both:

- the **skill source** (`mac-battery-band-guard/`)
- the packaged distributable artifact (`dist/mac-battery-band-guard.skill`)

If you want OpenClaw to discover the skill directly in the current workspace, place it under:

```text
<workspace>/skills/mac-battery-band-guard/
```

## Current status

This version now includes:

- adaptive monitoring
- ETA-aware reminders
- anomaly drain detection
- cycle-based de-duplication
- daily and weekly summaries
- profiles and quiet hours
- temporary upper-limit override
- habit-based suggestions
- local notifications
- Feishu push integration
- LaunchAgent background mode

## License

Add your preferred license if you want to make the repository reusable by others.
