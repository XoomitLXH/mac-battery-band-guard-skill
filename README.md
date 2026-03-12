# mac-battery-band-guard-skill

An adaptive macOS battery monitoring skill for keeping your Mac in the healthier **40%–80%** charging band.

It does **not** poll at a fixed high frequency. Instead, it estimates recent charge/discharge speed from real battery history and decides when it needs to check again.

## What it does

- reminds you to **charge soon** when the battery approaches the lower band
- reminds you to **charge now** when it falls below the floor
- reminds you to **stop charging** when it reaches the upper band
- adapts the next check interval from recent usage instead of using a noisy fixed loop
- supports:
  - local macOS notifications
  - optional **Feishu push notifications**
  - background installation via **LaunchAgent**

## Default behavior

- lower floor: **40%**
- early low-battery reminder: **45%**
- upper ceiling: **80%**
- low reminder reset: after rising back above **50%**
- stop-charging reminder reset: after dropping below **75%** in a later discharge cycle
- adaptive interval window: **5 to 180 minutes**

## Why this is adaptive instead of frequent polling

The monitor keeps a small local history of battery samples and uses that to estimate how quickly the battery is currently:

- discharging
- charging

From that slope, it computes an approximate ETA to the next important threshold:

- ETA to **40%** while discharging
- ETA to **80%** while charging

Then it chooses a longer or shorter sleep interval accordingly:

- far from thresholds → check less often
- close to thresholds → check more often
- heavy use naturally shortens future intervals
- light use naturally stretches them out

So the monitor follows real usage patterns without reading app-level activity or hammering the system.

## Reminder channels

### 1. Local macOS notifications

By default, reminders are shown with `osascript` / Notification Center.

### 2. Feishu push notifications

If you pass a Feishu target, the same reminder is also sent through OpenClaw to your Feishu chat.

Example:

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py install-launch-agent \
  --feishu-target ou_xxx
```

You can also run Feishu-only mode:

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py install-launch-agent \
  --feishu-target ou_xxx \
  --disable-local-notify
```

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

- `--lower <int>` — lower battery floor
- `--soon <int>` — early low-battery reminder threshold
- `--upper <int>` — upper stop-charging threshold
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

## OpenClaw integration

This repository contains both:

- the **skill source** (`mac-battery-band-guard/`)
- the packaged distributable file (`dist/mac-battery-band-guard.skill`)

If you want the current OpenClaw workspace to discover the skill directly, place the skill folder under:

```text
<workspace>/skills/mac-battery-band-guard/
```

## Current status

This version already includes:

- adaptive monitoring
- cycle-based de-duplication
- hysteresis resets
- local notifications
- Feishu push integration
- LaunchAgent background mode

## License

Add your preferred license if you want to make the repository reusable by others.
