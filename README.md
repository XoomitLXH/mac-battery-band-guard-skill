# mac-battery-band-guard

Adaptive macOS battery monitoring skill for keeping a Mac between **40% and 80%**.

## What it does

- warns around **45%** to charge soon
- warns at **40%** to charge now
- warns at **80%** to stop charging
- adapts its next check interval from recent charge/discharge speed instead of polling at a fixed cadence
- supports local macOS notifications, optional **Feishu push**, and LaunchAgent installation

## Contents

- `mac-battery-band-guard/` — the skill source
- `dist/mac-battery-band-guard.skill` — packaged distributable skill

## Main script

```bash
python3 mac-battery-band-guard/scripts/battery_guard.py once --print-only
python3 mac-battery-band-guard/scripts/battery_guard.py install-launch-agent
python3 mac-battery-band-guard/scripts/battery_guard.py install-launch-agent --feishu-target ou_xxx
python3 mac-battery-band-guard/scripts/battery_guard.py status
```
