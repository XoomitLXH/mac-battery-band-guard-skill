#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import plistlib
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

STATE_DIR = Path.home() / "Library" / "Application Support" / "mac-battery-band-guard"
STATE_FILE = STATE_DIR / "state.json"
DEFAULT_LABEL = "ai.openclaw.mac-battery-band-guard"
DEFAULT_LOWER = 40
DEFAULT_SOON = 45
DEFAULT_UPPER = 80
DEFAULT_RESET_LOW = 50
DEFAULT_RESET_HIGH = 75
DEFAULT_MIN_INTERVAL = 5
DEFAULT_MAX_INTERVAL = 180
MAX_HISTORY = 240
MAX_HISTORY_AGE_HOURS = 72


@dataclass
class BatterySample:
    ts: float
    percent: int
    state: str
    power_source: str
    raw: str


@dataclass
class GuardDecision:
    sample: BatterySample
    mode: str
    rate_pct_per_hour: float | None
    next_check_minutes: int
    notify: bool
    notify_key: str | None
    title: str | None
    body: str | None
    debug: dict[str, Any]


def run_cmd(command: list[str]) -> str:
    return subprocess.check_output(command, text=True).strip()


def read_battery() -> BatterySample:
    raw = run_cmd(["pmset", "-g", "batt"])
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("pmset returned no battery data")

    source_line = lines[0].lower()
    power_source = "ac" if "ac power" in source_line else "battery"
    detail = lines[-1]

    percent_match = re.search(r"(\d+)%", detail)
    if not percent_match:
        raise RuntimeError(f"Unable to parse battery percent from: {detail}")
    percent = int(percent_match.group(1))

    lowered = detail.lower()
    if "discharging" in lowered:
        state = "discharging"
    elif "charged" in lowered or "finishing charge" in lowered:
        state = "charged"
    elif re.search(r"\bcharging\b", lowered):
        state = "charging"
    elif power_source == "ac":
        state = "charging"
    else:
        state = "discharging"

    return BatterySample(
        ts=time.time(),
        percent=percent,
        state=state,
        power_source=power_source,
        raw=raw,
    )


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "history": [],
            "notifications": {},
            "cycles": {"discharge": 0, "charge": 0},
            "last_mode": None,
            "updated_at": None,
        }
    try:
        return json.loads(path.read_text())
    except Exception:
        return {
            "history": [],
            "notifications": {},
            "cycles": {"discharge": 0, "charge": 0},
            "last_mode": None,
            "updated_at": None,
        }


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))


def append_history(state: dict[str, Any], sample: BatterySample) -> None:
    history = state.setdefault("history", [])
    last = history[-1] if history else None
    if last:
        dt = sample.ts - float(last.get("ts", 0))
        unchanged = (
            int(last.get("percent", -1)) == sample.percent
            and last.get("state") == sample.state
            and last.get("power_source") == sample.power_source
        )
        if unchanged and dt < 15 * 60:
            last["ts"] = sample.ts
            last["raw"] = sample.raw
        else:
            history.append(asdict(sample))
    else:
        history.append(asdict(sample))

    cutoff = sample.ts - MAX_HISTORY_AGE_HOURS * 3600
    state["history"] = [item for item in history if float(item.get("ts", 0)) >= cutoff][-MAX_HISTORY:]


def update_cycles(state: dict[str, Any], sample: BatterySample) -> None:
    cycles = state.setdefault("cycles", {"discharge": 0, "charge": 0})
    notifications = state.setdefault("notifications", {})
    last_mode = state.get("last_mode")
    mode = normalize_mode(sample)
    if mode != last_mode:
        if mode == "discharging":
            cycles["discharge"] = int(cycles.get("discharge", 0)) + 1
            notifications.pop("stop_at_upper", None)
        elif mode in {"charging", "charged"}:
            cycles["charge"] = int(cycles.get("charge", 0)) + 1
            notifications.pop("charge_soon", None)
            notifications.pop("charge_now", None)
    state["last_mode"] = mode


def normalize_mode(sample: BatterySample) -> str:
    if sample.state in {"charging", "charged"} or sample.power_source == "ac":
        return "charging" if sample.percent < 100 and sample.state != "charged" else "charged"
    return "discharging"


def estimate_rate(history: list[dict[str, Any]], mode: str) -> float | None:
    if len(history) < 2:
        return None

    pairs: list[tuple[float, float, float]] = []
    now = float(history[-1]["ts"])
    for prev, curr in zip(history, history[1:]):
        prev_mode = normalize_mode(BatterySample(**prev))
        curr_mode = normalize_mode(BatterySample(**curr))
        if prev_mode != curr_mode or curr_mode != mode:
            continue
        dt_hours = (float(curr["ts"]) - float(prev["ts"])) / 3600
        if dt_hours <= 0.08 or dt_hours > 8:
            continue
        delta = float(curr["percent"]) - float(prev["percent"])
        if mode == "discharging" and delta >= 0:
            continue
        if mode in {"charging", "charged"} and delta <= 0:
            continue
        recency_weight = 1 + max(0.0, 1 - ((now - float(curr["ts"])) / (8 * 3600)))
        duration_weight = min(2.0, max(0.5, dt_hours))
        weight = recency_weight * duration_weight
        pairs.append((delta / dt_hours, weight, dt_hours))

    if not pairs:
        return None
    numerator = sum(rate * weight for rate, weight, _ in pairs)
    denominator = sum(weight for _, weight, _ in pairs)
    if denominator <= 0:
        return None
    return numerator / denominator


def human_duration(hours: float | None) -> str:
    if hours is None or math.isinf(hours) or math.isnan(hours):
        return "unknown time"
    minutes = max(1, round(hours * 60))
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    if m == 0:
        return f"{h}h"
    return f"{h}h {m}m"


def choose_interval(sample: BatterySample, rate: float | None, lower: int, upper: int, min_minutes: int, max_minutes: int) -> tuple[int, dict[str, Any]]:
    mode = normalize_mode(sample)
    debug: dict[str, Any] = {"mode": mode, "rate_pct_per_hour": rate}

    if mode == "discharging":
        distance = sample.percent - lower
        if rate is not None and rate < 0:
            eta_hours = max(0.0, distance / abs(rate)) if distance > 0 else 0.0
            debug["eta_to_lower_hours"] = eta_hours
            if eta_hours <= 0.25:
                minutes = min_minutes
            elif eta_hours <= 0.5:
                minutes = 8
            elif eta_hours <= 1:
                minutes = 12
            elif eta_hours <= 2:
                minutes = 20
            elif eta_hours <= 4:
                minutes = 35
            elif eta_hours <= 8:
                minutes = 60
            else:
                minutes = 120
        else:
            debug["eta_to_lower_hours"] = None
            if distance <= 0:
                minutes = 10
            elif distance <= 5:
                minutes = 15
            elif distance <= 10:
                minutes = 30
            elif distance <= 20:
                minutes = 60
            else:
                minutes = 120
    else:
        distance = upper - sample.percent
        if rate is not None and rate > 0:
            eta_hours = max(0.0, distance / rate) if distance > 0 else 0.0
            debug["eta_to_upper_hours"] = eta_hours
            if eta_hours <= 0.25:
                minutes = min_minutes
            elif eta_hours <= 0.5:
                minutes = 8
            elif eta_hours <= 1:
                minutes = 12
            elif eta_hours <= 2:
                minutes = 20
            elif eta_hours <= 4:
                minutes = 35
            else:
                minutes = 75
        else:
            debug["eta_to_upper_hours"] = None
            if distance <= 0:
                minutes = 20
            elif distance <= 5:
                minutes = 15
            elif distance <= 10:
                minutes = 25
            elif distance <= 20:
                minutes = 45
            else:
                minutes = 75

    minutes = max(min_minutes, min(max_minutes, int(minutes)))
    debug["next_interval_minutes"] = minutes
    return minutes, debug


def maybe_notify(
    state: dict[str, Any],
    sample: BatterySample,
    rate: float | None,
    lower: int,
    soon: int,
    upper: int,
    reset_low: int,
    reset_high: int,
) -> tuple[bool, str | None, str | None, str | None]:
    notifications = state.setdefault("notifications", {})
    cycles = state.setdefault("cycles", {"discharge": 0, "charge": 0})
    mode = normalize_mode(sample)

    if mode in {"charging", "charged"} and sample.percent >= reset_low:
        notifications.pop("charge_soon", None)
        notifications.pop("charge_now", None)
    if mode == "discharging" and sample.percent <= reset_high:
        # keep stop notification until the user unplugs and drops below reset_high
        pass
    if mode == "discharging" and sample.percent < reset_high:
        notifications.pop("stop_at_upper", None)

    if mode == "discharging":
        eta = None
        if rate is not None and rate < 0 and sample.percent > lower:
            eta = (sample.percent - lower) / abs(rate)
        cycle = int(cycles.get("discharge", 0))

        if sample.percent <= lower:
            last_cycle = notifications.get("charge_now", {}).get("cycle")
            if last_cycle != cycle:
                notifications["charge_now"] = {"cycle": cycle, "ts": sample.ts}
                title = "Battery Guard · Charge now"
                body = f"Battery is at {sample.percent}%. This is at or below the {lower}% floor — plug in soon."
                return True, "charge_now", title, body
        elif sample.percent <= soon:
            last_cycle = notifications.get("charge_soon", {}).get("cycle")
            if last_cycle != cycle:
                notifications["charge_soon"] = {"cycle": cycle, "ts": sample.ts}
                eta_text = human_duration(eta)
                title = "Battery Guard · Charge soon"
                body = f"Battery is at {sample.percent}%. At the current pace it may reach {lower}% in about {eta_text}."
                return True, "charge_soon", title, body

    if mode in {"charging", "charged"} and sample.percent >= upper:
        cycle = int(cycles.get("charge", 0))
        last_cycle = notifications.get("stop_at_upper", {}).get("cycle")
        if last_cycle != cycle:
            notifications["stop_at_upper"] = {"cycle": cycle, "ts": sample.ts}
            title = "Battery Guard · Stop charging"
            body = f"Battery reached {sample.percent}%. This is at or above the {upper}% ceiling — unplug when convenient."
            return True, "stop_at_upper", title, body

    return False, None, None, None


def build_decision(
    state: dict[str, Any],
    sample: BatterySample,
    lower: int,
    soon: int,
    upper: int,
    reset_low: int,
    reset_high: int,
    min_minutes: int,
    max_minutes: int,
) -> GuardDecision:
    history = state.get("history", [])
    mode = normalize_mode(sample)
    rate = estimate_rate(history, mode)
    next_check_minutes, debug = choose_interval(sample, rate, lower, upper, min_minutes, max_minutes)
    notify, notify_key, title, body = maybe_notify(
        state=state,
        sample=sample,
        rate=rate,
        lower=lower,
        soon=soon,
        upper=upper,
        reset_low=reset_low,
        reset_high=reset_high,
    )
    return GuardDecision(
        sample=sample,
        mode=mode,
        rate_pct_per_hour=rate,
        next_check_minutes=next_check_minutes,
        notify=notify,
        notify_key=notify_key,
        title=title,
        body=body,
        debug=debug,
    )


def send_notification(title: str, body: str) -> None:
    script = f'display notification {json.dumps(body)} with title {json.dumps(title)}'
    subprocess.run(["osascript", "-e", script], check=False)


def send_feishu_message(target: str, title: str, body: str, account: str | None = None) -> bool:
    message = f"🔋 {title}\n{body}"
    command = [
        "openclaw",
        "message",
        "send",
        "--channel",
        "feishu",
        "--target",
        target,
        "--message",
        message,
        "--json",
    ]
    if account:
        command.extend(["--account", account])
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.stderr.write(result.stderr or result.stdout)
        return False
    return True


def dispatch_notifications(args: argparse.Namespace, decision: GuardDecision) -> None:
    if not decision.notify or not decision.title or not decision.body or args.print_only:
        return
    if not args.disable_local_notify:
        send_notification(decision.title, decision.body)
    if args.feishu_target:
        send_feishu_message(
            target=args.feishu_target,
            title=decision.title,
            body=decision.body,
            account=args.feishu_account,
        )


def state_snapshot(state: dict[str, Any], decision: GuardDecision) -> dict[str, Any]:
    return {
        "sample": asdict(decision.sample),
        "mode": decision.mode,
        "rate_pct_per_hour": decision.rate_pct_per_hour,
        "next_check_minutes": decision.next_check_minutes,
        "notify": decision.notify,
        "notify_key": decision.notify_key,
        "title": decision.title,
        "body": decision.body,
        "debug": decision.debug,
        "history_size": len(state.get("history", [])),
        "notifications": state.get("notifications", {}),
        "cycles": state.get("cycles", {}),
        "updated_at": state.get("updated_at"),
    }


def do_once(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    sample = read_battery()
    append_history(state, sample)
    update_cycles(state, sample)
    decision = build_decision(
        state=state,
        sample=sample,
        lower=args.lower,
        soon=args.soon,
        upper=args.upper,
        reset_low=args.reset_low,
        reset_high=args.reset_high,
        min_minutes=args.min_interval,
        max_minutes=args.max_interval,
    )
    state["updated_at"] = sample.ts
    save_state(args.state_file, state)

    dispatch_notifications(args, decision)

    print(json.dumps(state_snapshot(state, decision), ensure_ascii=False, indent=2))
    return 0


def do_run(args: argparse.Namespace) -> int:
    while True:
        state = load_state(args.state_file)
        sample = read_battery()
        append_history(state, sample)
        update_cycles(state, sample)
        decision = build_decision(
            state=state,
            sample=sample,
            lower=args.lower,
            soon=args.soon,
            upper=args.upper,
            reset_low=args.reset_low,
            reset_high=args.reset_high,
            min_minutes=args.min_interval,
            max_minutes=args.max_interval,
        )
        state["updated_at"] = sample.ts
        save_state(args.state_file, state)

        snapshot = state_snapshot(state, decision)
        print(json.dumps(snapshot, ensure_ascii=False), flush=True)

        dispatch_notifications(args, decision)

        time.sleep(decision.next_check_minutes * 60)


def launch_agent_plist(args: argparse.Namespace) -> dict[str, Any]:
    python_bin = sys.executable or "/usr/bin/python3"
    script_path = Path(__file__).resolve()
    log_dir = args.state_file.parent
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "guard.stdout.log"
    stderr_path = log_dir / "guard.stderr.log"
    return {
        "Label": args.label,
        "ProgramArguments": [
            python_bin,
            str(script_path),
            "run",
            "--state-file",
            str(args.state_file),
            "--lower",
            str(args.lower),
            "--soon",
            str(args.soon),
            "--upper",
            str(args.upper),
            "--reset-low",
            str(args.reset_low),
            "--reset-high",
            str(args.reset_high),
            "--min-interval",
            str(args.min_interval),
            "--max-interval",
            str(args.max_interval),
        ]
        + (["--print-only"] if args.print_only else [])
        + (["--disable-local-notify"] if args.disable_local_notify else [])
        + (["--feishu-target", args.feishu_target] if args.feishu_target else [])
        + (["--feishu-account", args.feishu_account] if args.feishu_account else []),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        },
    }


def do_install(args: argparse.Namespace) -> int:
    plist = launch_agent_plist(args)
    dest = Path.home() / "Library" / "LaunchAgents" / f"{args.label}.plist"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        plistlib.dump(plist, fh, sort_keys=False)

    subprocess.run(["launchctl", "unload", str(dest)], check=False)
    subprocess.run(["launchctl", "load", str(dest)], check=False)
    print(dest)
    return 0


def do_uninstall(args: argparse.Namespace) -> int:
    dest = Path.home() / "Library" / "LaunchAgents" / f"{args.label}.plist"
    subprocess.run(["launchctl", "unload", str(dest)], check=False)
    if dest.exists():
        dest.unlink()
    return 0


def do_status(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


def add_shared_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-file", type=Path, default=STATE_FILE)
    parser.add_argument("--lower", type=int, default=DEFAULT_LOWER)
    parser.add_argument("--soon", type=int, default=DEFAULT_SOON)
    parser.add_argument("--upper", type=int, default=DEFAULT_UPPER)
    parser.add_argument("--reset-low", type=int, default=DEFAULT_RESET_LOW)
    parser.add_argument("--reset-high", type=int, default=DEFAULT_RESET_HIGH)
    parser.add_argument("--min-interval", type=int, default=DEFAULT_MIN_INTERVAL)
    parser.add_argument("--max-interval", type=int, default=DEFAULT_MAX_INTERVAL)
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--disable-local-notify", action="store_true")
    parser.add_argument("--feishu-target")
    parser.add_argument("--feishu-account")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Adaptive 40-80 macOS battery guard")
    sub = parser.add_subparsers(dest="command", required=True)

    once = sub.add_parser("once", help="Sample battery once and print a decision JSON")
    add_shared_args(once)
    once.set_defaults(func=do_once)

    run = sub.add_parser("run", help="Run continuously with adaptive sleep intervals")
    add_shared_args(run)
    run.set_defaults(func=do_run)

    install = sub.add_parser("install-launch-agent", help="Install a per-user LaunchAgent")
    add_shared_args(install)
    install.add_argument("--label", default=DEFAULT_LABEL)
    install.set_defaults(func=do_install)

    uninstall = sub.add_parser("uninstall-launch-agent", help="Remove the LaunchAgent")
    uninstall.add_argument("--label", default=DEFAULT_LABEL)
    uninstall.add_argument("--state-file", type=Path, default=STATE_FILE)
    uninstall.set_defaults(func=do_uninstall)

    status = sub.add_parser("status", help="Print the persisted guard state")
    status.add_argument("--state-file", type=Path, default=STATE_FILE)
    status.set_defaults(func=do_status)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
