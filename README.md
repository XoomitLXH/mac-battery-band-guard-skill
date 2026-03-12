# mac-battery-band-guard-skill

An adaptive macOS battery monitoring skill for keeping your Mac in a healthier charging band while still being practical in real life.

This project started as a 40%–80% reminder tool, then grew into a fuller battery assistant with:

- threshold reminders
- ETA prediction
- anomaly detection
- daily / weekly summaries
- profile switching
- quiet hours
- temporary upper-limit overrides
- habit-learning style suggestions
- local macOS notifications
- optional Feishu push notifications

It does **not** poll at a fixed high frequency. Instead, it watches recent battery history, estimates the current slope, and chooses its next check interval from that behavior.

## What it does

### Core reminders

- reminds you to **charge soon** as you approach the lower band
- reminds you to **charge now** when you drop below the floor
- becomes more aggressive below **41%** with harsher wording and repeated nudges
- reminds you to **stop charging** when you hit the upper ceiling
- becomes more aggressive above the charging ceiling with repeated unplug reminders

### Smarter battery behavior

- predicts ETA to 40% / 80%
- explains reminders in more natural language
- detects unusually fast battery drain compared with your typical pattern

### Longer-term usefulness

- sends daily battery summaries
- sends weekly summary + habit suggestions
- learns simple patterns from recent battery history

### Lifestyle features

- profiles: `default`, `work`, `travel`, `night`, `auto`
- quiet hours
- automatic day/night switching in `auto` mode
- temporary upper-limit override (for travel / special days)

### Delivery

- local macOS notifications
- optional Feishu push through OpenClaw
- background LaunchAgent mode

## Default profiles

### `default`
Balanced 40–80 behavior.

### `work`
Workday-oriented timing and summaries, still focused on staying in the healthy band.

### `travel`
Allows a higher charging ceiling so the Mac can leave home with more battery.

### `night`
Adds quiet-hours behavior and a slightly more conservative low-battery posture.

### `auto`
Uses one profile during the day and another during quiet hours, so you do not need to switch manually.
Default auto mapping:

- day → `work`
- quiet hours → `night`

## How the adaptive algorithm works

The script stores a rolling history of battery samples and computes recent charging / discharging speed from adjacent samples in the same mode.

From that it can estimate:

- current discharge pace
- current charge pace
- ETA to the lower threshold
- ETA to the upper threshold

Then it adjusts its next sleep interval:

- far from thresholds → sleep longer
- near thresholds → check sooner
- heavy use naturally shortens intervals
- light use stretches them out

So the monitor reacts to actual usage patterns instead of running a noisy fixed loop.

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

## Main commands

### Dry run one sample

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

### Show raw state

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py status
```

### Show report / insights

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py report
```

## Mode / profile commands

### Switch profile

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py set-profile work
python3 mac-battery-band-guard/scripts/battery_guard.py set-profile travel
python3 mac-battery-band-guard/scripts/battery_guard.py set-profile night
python3 mac-battery-band-guard/scripts/battery_guard.py set-profile auto
```

### Configure auto mode

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py set-auto-profiles \
  --day-profile work \
  --quiet-profile night
```

### One-shot travel mode

Turn on temporary travel behavior for a limited time and let it expire automatically:

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py start-trip --hours 12 --upper 95 --set-profile-auto
```

End it early:

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py end-trip
```

### Test the reminder pipeline

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py test-alert --feishu-target ou_xxx
```

### Set quiet hours

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py set-quiet-hours 23-08
```

### Temporary upper override

Raise the upper limit temporarily for travel or a long unplugged day:

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py set-temp-upper 95 --hours 12
```

Clear it again:

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py clear-temp-upper
```

## Important flags

- `--profile <default|work|travel|night>`
- `--quiet-hours <HH-HH>`
- `--summary-hour <0-23>`
- `--weekly-summary-weekday <0-6>`
- `--lower <int>`
- `--soon <int>`
- `--upper <int>`
- `--min-interval <minutes>`
- `--max-interval <minutes>`
- `--print-only`
- `--disable-local-notify`
- `--feishu-target <open_id>`
- `--feishu-account <id>`
- `--state-file <path>`

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

This repo includes both:

- the skill source folder
- the packaged `.skill` artifact

To let the current OpenClaw workspace discover it directly, place the skill under:

```text
<workspace>/skills/mac-battery-band-guard/
```

## Current status

This upgraded version now includes:

- adaptive threshold monitoring
- ETA prediction
- anomaly detection
- richer reminder language
- daily summary
- weekly summary with suggestions
- profile switching
- quiet hours
- temporary upper override
- Feishu push integration
- LaunchAgent background mode

## License

Add your preferred license if you want to publish it for wider reuse.
