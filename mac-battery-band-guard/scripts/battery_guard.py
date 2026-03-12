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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

STATE_DIR = Path.home() / "Library" / "Application Support" / "mac-battery-band-guard"
STATE_FILE = STATE_DIR / "state.json"
DEFAULT_LABEL = "ai.openclaw.mac-battery-band-guard"
MAX_HISTORY = 1500
MAX_HISTORY_AGE_HOURS = 24 * 21
MAX_EVENTS = 800
PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "balanced": {
        "lower": 40,
        "soon": 45,
        "upper": 80,
        "reset_low": 50,
        "reset_high": 75,
        "min_interval": 5,
        "max_interval": 180,
        "quiet_hours": None,
    },
    "work": {
        "lower": 40,
        "soon": 46,
        "upper": 80,
        "reset_low": 52,
        "reset_high": 75,
        "min_interval": 5,
        "max_interval": 150,
        "quiet_hours": None,
    },
    "outing": {
        "lower": 35,
        "soon": 45,
        "upper": 95,
        "reset_low": 50,
        "reset_high": 85,
        "min_interval": 5,
        "max_interval": 120,
        "quiet_hours": None,
    },
    "night": {
        "lower": 40,
        "soon": 45,
        "upper": 80,
        "reset_low": 50,
        "reset_high": 75,
        "min_interval": 5,
        "max_interval": 180,
        "quiet_hours": "23:00-08:00",
    },
}


@dataclass
class BatterySample:
    ts: float
    percent: int
    state: str
    power_source: str
    raw: str


@dataclass
class GuardConfig:
    profile: str
    lower: int
    soon: int
    upper: int
    reset_low: int
    reset_high: int
    min_interval: int
    max_interval: int
    quiet_hours: str | None
    daily_summary_hour: int
    weekly_summary_weekday: int
    weekly_summary_hour: int
    disable_local_notify: bool
    feishu_target: str | None
    feishu_account: str | None
    print_only: bool


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
    severity: str
    debug: dict[str, Any]


def run_cmd(command: list[str]) -> str:
    return subprocess.check_output(command, text=True).strip()


def now_local(ts: float | None = None) -> datetime:
    return datetime.fromtimestamp(ts or time.time())


def iso_local(ts: float | None = None) -> str:
    return now_local(ts).isoformat(timespec="seconds")


def parse_hour_minute(value: str) -> tuple[int, int]:
    hour, minute = value.split(":", 1)
    return int(hour), int(minute)


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


def default_state() -> dict[str, Any]:
    return {
        "history": [],
        "events": [],
        "notifications": {},
        "cycles": {"discharge": 0, "charge": 0},
        "last_mode": None,
        "updated_at": None,
        "profile": "balanced",
        "temporary_upper": None,
        "summary_sent": {"daily": None, "weekly": None},
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_state()
    try:
        data = json.loads(path.read_text())
        merged = default_state()
        merged.update(data)
        return merged
    except Exception:
        return default_state()


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))


def normalize_mode(sample: BatterySample) -> str:
    if sample.state in {"charging", "charged"} or sample.power_source == "ac":
        return "charging" if sample.percent < 100 and sample.state != "charged" else "charged"
    return "discharging"


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
            notifications.pop("anomaly_fast_drain", None)
        elif mode in {"charging", "charged"}:
            cycles["charge"] = int(cycles.get("charge", 0)) + 1
            notifications.pop("charge_soon", None)
            notifications.pop("charge_now", None)
    state["last_mode"] = mode


def iter_mode_pairs(history: list[dict[str, Any]], mode: str) -> Iterable[tuple[dict[str, Any], dict[str, Any], float, float]]:
    for prev, curr in zip(history, history[1:]):
        prev_mode = normalize_mode(BatterySample(**prev))
        curr_mode = normalize_mode(BatterySample(**curr))
        if prev_mode != curr_mode or curr_mode != mode:
            continue
        dt_hours = (float(curr["ts"]) - float(prev["ts"])) / 3600
        if dt_hours <= 0.03 or dt_hours > 8:
            continue
        delta = float(curr["percent"]) - float(prev["percent"])
        yield prev, curr, dt_hours, delta


def estimate_rate(history: list[dict[str, Any]], mode: str, lookback_hours: float = 8) -> float | None:
    if len(history) < 2:
        return None

    pairs: list[tuple[float, float]] = []
    now = float(history[-1]["ts"])
    for _prev, curr, dt_hours, delta in iter_mode_pairs(history, mode):
        age_hours = (now - float(curr["ts"])) / 3600
        if age_hours > lookback_hours:
            continue
        if mode == "discharging" and delta >= 0:
            continue
        if mode in {"charging", "charged"} and delta <= 0:
            continue
        recency_weight = 1 + max(0.0, 1 - (age_hours / max(1.0, lookback_hours)))
        duration_weight = min(2.0, max(0.5, dt_hours))
        pairs.append((delta / dt_hours, recency_weight * duration_weight))

    if not pairs:
        return None
    numerator = sum(rate * weight for rate, weight in pairs)
    denominator = sum(weight for _, weight in pairs)
    return numerator / denominator if denominator > 0 else None


def average_rate(history: list[dict[str, Any]], mode: str, since_hours: float) -> float | None:
    now = time.time()
    values: list[float] = []
    for _prev, curr, dt_hours, delta in iter_mode_pairs(history, mode):
        if now - float(curr["ts"]) > since_hours * 3600:
            continue
        if mode == "discharging" and delta < 0:
            values.append(abs(delta / dt_hours))
        elif mode in {"charging", "charged"} and delta > 0:
            values.append(delta / dt_hours)
    if not values:
        return None
    return sum(values) / len(values)


def human_duration(hours: float | None) -> str:
    if hours is None or math.isinf(hours) or math.isnan(hours):
        return "unknown time"
    minutes = max(1, round(hours * 60))
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h" if m == 0 else f"{h}h {m}m"


def is_quiet_hours(quiet_hours: str | None, ts: float | None = None) -> bool:
    if not quiet_hours:
        return False
    try:
        start_raw, end_raw = quiet_hours.split("-", 1)
        start_h, start_m = parse_hour_minute(start_raw)
        end_h, end_m = parse_hour_minute(end_raw)
    except Exception:
        return False
    current = now_local(ts)
    current_minutes = current.hour * 60 + current.minute
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    if start_minutes == end_minutes:
        return False
    if start_minutes < end_minutes:
        return start_minutes <= current_minutes < end_minutes
    return current_minutes >= start_minutes or current_minutes < end_minutes


def choose_interval(sample: BatterySample, rate: float | None, lower: int, upper: int, min_minutes: int, max_minutes: int) -> tuple[int, dict[str, Any]]:
    mode = normalize_mode(sample)
    debug: dict[str, Any] = {"mode": mode, "rate_pct_per_hour": rate}

    if mode == "discharging":
        distance = sample.percent - lower
        if rate is not None and rate < 0:
            eta_hours = max(0.0, distance / abs(rate)) if distance > 0 else 0.0
            debug["eta_to_lower_hours"] = eta_hours
            if eta_hours <= 0.20:
                minutes = min_minutes
            elif eta_hours <= 0.5:
                minutes = 7
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
            minutes = 10 if distance <= 0 else 15 if distance <= 5 else 30 if distance <= 10 else 60 if distance <= 20 else 120
    else:
        distance = upper - sample.percent
        if rate is not None and rate > 0:
            eta_hours = max(0.0, distance / rate) if distance > 0 else 0.0
            debug["eta_to_upper_hours"] = eta_hours
            if eta_hours <= 0.20:
                minutes = min_minutes
            elif eta_hours <= 0.5:
                minutes = 7
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
            minutes = 20 if distance <= 0 else 15 if distance <= 5 else 25 if distance <= 10 else 45 if distance <= 20 else 75

    minutes = max(min_minutes, min(max_minutes, int(minutes)))
    debug["next_interval_minutes"] = minutes
    return minutes, debug


def describe_pace(rate: float | None, baseline: float | None, mode: str) -> str:
    if rate is None:
        return "pace is still being learned"
    if baseline is None:
        speed = abs(rate) if mode == "discharging" else rate
        return f"current pace is about {speed:.1f}%/h"
    current = abs(rate) if mode == "discharging" else rate
    if baseline <= 0:
        return f"current pace is about {current:.1f}%/h"
    ratio = current / baseline
    if ratio >= 1.6:
        return f"which is much faster than your recent norm ({baseline:.1f}%/h)"
    if ratio >= 1.2:
        return f"which is a bit faster than your recent norm ({baseline:.1f}%/h)"
    if ratio <= 0.7:
        return f"which is slower than your recent norm ({baseline:.1f}%/h)"
    return f"which is close to your usual pace ({baseline:.1f}%/h)"


def append_event(state: dict[str, Any], key: str, title: str, body: str, kind: str = "notification") -> None:
    events = state.setdefault("events", [])
    events.append({"ts": time.time(), "key": key, "title": title, "body": body, "kind": kind})
    state["events"] = events[-MAX_EVENTS:]


def detect_anomaly(history: list[dict[str, Any]], sample: BatterySample, rate: float | None, lower: int) -> tuple[bool, str | None]:
    if normalize_mode(sample) != "discharging" or rate is None or rate >= 0:
        return False, None
    if sample.percent <= lower + 3:
        return False, None
    current = abs(rate)
    baseline = average_rate(history, "discharging", since_hours=24 * 7)
    if baseline is None:
        return False, None
    if current >= max(baseline * 1.8, baseline + 6, 18):
        return True, f"Battery is draining at about {current:.1f}%/h, well above your recent baseline of {baseline:.1f}%/h."
    return False, None


def notification_allowed(config: GuardConfig, severity: str, ts: float) -> tuple[bool, bool]:
    quiet = is_quiet_hours(config.quiet_hours, ts)
    if not quiet:
        return True, True
    # During quiet hours, suppress local notifications unless critical.
    local_allowed = severity == "critical"
    feishu_allowed = severity in {"warning", "critical", "summary"}
    return local_allowed, feishu_allowed


def maybe_notify(
    state: dict[str, Any],
    sample: BatterySample,
    rate: float | None,
    lower: int,
    soon: int,
    upper: int,
    reset_low: int,
    reset_high: int,
) -> tuple[bool, str | None, str | None, str | None, str]:
    notifications = state.setdefault("notifications", {})
    cycles = state.setdefault("cycles", {"discharge": 0, "charge": 0})
    history = state.get("history", [])
    mode = normalize_mode(sample)
    baseline_discharge = average_rate(history, "discharging", since_hours=24 * 7)
    baseline_charge = average_rate(history, "charging", since_hours=24 * 7)

    if mode in {"charging", "charged"} and sample.percent >= reset_low:
        notifications.pop("charge_soon", None)
        notifications.pop("charge_now", None)
    if mode == "discharging" and sample.percent < reset_high:
        notifications.pop("stop_at_upper", None)

    anomaly, anomaly_text = detect_anomaly(history, sample, rate, lower)
    if anomaly:
        cycle = int(cycles.get("discharge", 0))
        last_cycle = notifications.get("anomaly_fast_drain", {}).get("cycle")
        if last_cycle != cycle:
            notifications["anomaly_fast_drain"] = {"cycle": cycle, "ts": sample.ts}
            return (
                True,
                "anomaly_fast_drain",
                "Battery Guard · Unusual battery drain",
                f"Battery is at {sample.percent}%, and today it is dropping unusually fast — {anomaly_text}",
                "warning",
            )

    if mode == "discharging":
        eta = (sample.percent - lower) / abs(rate) if rate is not None and rate < 0 and sample.percent > lower else None
        cycle = int(cycles.get("discharge", 0))
        pace = describe_pace(rate, baseline_discharge, mode)

        if sample.percent <= lower:
            last_cycle = notifications.get("charge_now", {}).get("cycle")
            if last_cycle != cycle:
                notifications["charge_now"] = {"cycle": cycle, "ts": sample.ts}
                title = "Battery Guard · Charge now"
                body = f"Battery is at {sample.percent}% — below your {lower}% floor. {pace}. Plug in soon."
                return True, "charge_now", title, body, "critical"
        elif sample.percent <= soon:
            last_cycle = notifications.get("charge_soon", {}).get("cycle")
            if last_cycle != cycle:
                notifications["charge_soon"] = {"cycle": cycle, "ts": sample.ts}
                eta_text = human_duration(eta)
                title = "Battery Guard · Charge soon"
                body = f"Battery is at {sample.percent}%. If this pace holds, it may reach {lower}% in about {eta_text}, {pace}."
                return True, "charge_soon", title, body, "warning"

    if mode in {"charging", "charged"} and sample.percent >= upper:
        cycle = int(cycles.get("charge", 0))
        last_cycle = notifications.get("stop_at_upper", {}).get("cycle")
        if last_cycle != cycle:
            notifications["stop_at_upper"] = {"cycle": cycle, "ts": sample.ts}
            pace = describe_pace(rate, baseline_charge, mode)
            title = "Battery Guard · Stop charging"
            body = f"Battery reached {sample.percent}%, above your {upper}% ceiling. {pace}. You can unplug when convenient."
            return True, "stop_at_upper", title, body, "warning"

    return False, None, None, None, "info"


def apply_profile_overrides(state: dict[str, Any], requested_profile: str | None) -> str:
    if requested_profile:
        state["profile"] = requested_profile
    return state.get("profile") or "balanced"


def effective_upper(profile: str, state: dict[str, Any], explicit_upper: int | None) -> int:
    if explicit_upper is not None:
        return explicit_upper
    temp = state.get("temporary_upper") or {}
    until = temp.get("until_ts")
    if until and float(until) > time.time():
        return int(temp.get("percent", PROFILE_DEFAULTS[profile]["upper"]))
    if temp:
        state["temporary_upper"] = None
    return int(PROFILE_DEFAULTS[profile]["upper"])


def build_config(args: argparse.Namespace, state: dict[str, Any]) -> GuardConfig:
    profile = apply_profile_overrides(state, getattr(args, "profile", None))
    defaults = PROFILE_DEFAULTS[profile]
    upper = effective_upper(profile, state, getattr(args, "upper", None))
    quiet = getattr(args, "quiet_hours", None)
    if quiet is None:
        quiet = defaults.get("quiet_hours")
    return GuardConfig(
        profile=profile,
        lower=getattr(args, "lower", None) or int(defaults["lower"]),
        soon=getattr(args, "soon", None) or int(defaults["soon"]),
        upper=upper,
        reset_low=getattr(args, "reset_low", None) or int(defaults["reset_low"]),
        reset_high=getattr(args, "reset_high", None) or int(defaults["reset_high"]),
        min_interval=getattr(args, "min_interval", None) or int(defaults["min_interval"]),
        max_interval=getattr(args, "max_interval", None) or int(defaults["max_interval"]),
        quiet_hours=quiet,
        daily_summary_hour=(getattr(args, "daily_summary_hour", None) if getattr(args, "daily_summary_hour", None) is not None else 21),
        weekly_summary_weekday=(getattr(args, "weekly_summary_weekday", None) if getattr(args, "weekly_summary_weekday", None) is not None else 6),
        weekly_summary_hour=(getattr(args, "weekly_summary_hour", None) if getattr(args, "weekly_summary_hour", None) is not None else 21),
        disable_local_notify=getattr(args, "disable_local_notify", False),
        feishu_target=getattr(args, "feishu_target", None),
        feishu_account=getattr(args, "feishu_account", None),
        print_only=getattr(args, "print_only", False),
    )


def build_decision(state: dict[str, Any], sample: BatterySample, config: GuardConfig) -> GuardDecision:
    history = state.get("history", [])
    mode = normalize_mode(sample)
    rate = estimate_rate(history, mode)
    next_check_minutes, debug = choose_interval(
        sample,
        rate,
        config.lower,
        config.upper,
        config.min_interval,
        config.max_interval,
    )
    notify, notify_key, title, body, severity = maybe_notify(
        state=state,
        sample=sample,
        rate=rate,
        lower=config.lower,
        soon=config.soon,
        upper=config.upper,
        reset_low=config.reset_low,
        reset_high=config.reset_high,
    )
    debug["profile"] = config.profile
    debug["quiet_hours"] = config.quiet_hours
    return GuardDecision(
        sample=sample,
        mode=mode,
        rate_pct_per_hour=rate,
        next_check_minutes=next_check_minutes,
        notify=notify,
        notify_key=notify_key,
        title=title,
        body=body,
        severity=severity,
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


def dispatch_message(config: GuardConfig, title: str, body: str, severity: str, ts: float) -> tuple[bool, bool]:
    if config.print_only:
        return False, False
    local_allowed, feishu_allowed = notification_allowed(config, severity, ts)
    local_sent = False
    feishu_sent = False
    if local_allowed and not config.disable_local_notify:
        send_notification(title, body)
        local_sent = True
    if feishu_allowed and config.feishu_target:
        feishu_sent = send_feishu_message(config.feishu_target, title, body, config.feishu_account)
    return local_sent, feishu_sent


def history_slice(history: list[dict[str, Any]], since_hours: float) -> list[dict[str, Any]]:
    cutoff = time.time() - since_hours * 3600
    return [item for item in history if float(item.get("ts", 0)) >= cutoff]


def summarize_period(history: list[dict[str, Any]], events: list[dict[str, Any]], hours: float) -> dict[str, Any]:
    relevant = history_slice(history, hours)
    if not relevant:
        return {
            "min_percent": None,
            "max_percent": None,
            "avg_discharge_rate": None,
            "avg_charge_rate": None,
            "low_alerts": 0,
            "stop_alerts": 0,
        }
    cutoff = time.time() - hours * 3600
    relevant_events = [e for e in events if float(e.get("ts", 0)) >= cutoff]
    return {
        "min_percent": min(int(item["percent"]) for item in relevant),
        "max_percent": max(int(item["percent"]) for item in relevant),
        "avg_discharge_rate": average_rate(relevant, "discharging", since_hours=hours),
        "avg_charge_rate": average_rate(relevant, "charging", since_hours=hours),
        "low_alerts": sum(1 for e in relevant_events if e.get("key") in {"charge_soon", "charge_now"}),
        "stop_alerts": sum(1 for e in relevant_events if e.get("key") == "stop_at_upper"),
        "anomaly_alerts": sum(1 for e in relevant_events if e.get("key") == "anomaly_fast_drain"),
    }


def build_daily_summary(state: dict[str, Any], config: GuardConfig) -> tuple[str, str]:
    summary = summarize_period(state.get("history", []), state.get("events", []), 24)
    title = "Battery Guard · Daily summary"
    body = (
        f"Past 24h: low {summary['min_percent']}%, high {summary['max_percent']}%, "
        f"avg drain {summary['avg_discharge_rate']:.1f}%/h. " if summary["avg_discharge_rate"] is not None else
        f"Past 24h: low {summary['min_percent']}%, high {summary['max_percent']}%. "
    )
    extras = []
    if summary.get("low_alerts"):
        extras.append(f"low alerts: {summary['low_alerts']}")
    if summary.get("stop_alerts"):
        extras.append(f"stop-charge alerts: {summary['stop_alerts']}")
    if summary.get("anomaly_alerts"):
        extras.append(f"anomaly alerts: {summary['anomaly_alerts']}")
    if config.profile == "outing":
        extras.append("outing profile is active")
    elif config.profile == "night":
        extras.append("night profile is active")
    if extras:
        body += " · ".join(extras)
    return title, body.strip()


def build_weekly_summary(state: dict[str, Any]) -> tuple[str, str]:
    summary = summarize_period(state.get("history", []), state.get("events", []), 24 * 7)
    title = "Battery Guard · Weekly summary"
    body = (
        f"Past 7d: low {summary['min_percent']}%, high {summary['max_percent']}%, "
        f"avg drain {summary['avg_discharge_rate']:.1f}%/h, avg charge {summary['avg_charge_rate']:.1f}%/h. "
        if summary["avg_discharge_rate"] is not None and summary["avg_charge_rate"] is not None
        else f"Past 7d: low {summary['min_percent']}%, high {summary['max_percent']}. "
    )
    body += (
        f"Low alerts: {summary['low_alerts']}, stop-charge alerts: {summary['stop_alerts']}, "
        f"anomaly alerts: {summary['anomaly_alerts']}."
    )
    return title, body


def maybe_send_summaries(state: dict[str, Any], config: GuardConfig) -> list[dict[str, str]]:
    sent: list[dict[str, str]] = []
    now = now_local()
    sent_state = state.setdefault("summary_sent", {"daily": None, "weekly": None})

    day_key = now.strftime("%Y-%m-%d")
    if now.hour >= config.daily_summary_hour and sent_state.get("daily") != day_key:
        title, body = build_daily_summary(state, config)
        dispatch_message(config, title, body, "summary", time.time())
        append_event(state, "daily_summary", title, body, kind="summary")
        sent_state["daily"] = day_key
        sent.append({"title": title, "body": body})

    week_key = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    if now.weekday() == config.weekly_summary_weekday and now.hour >= config.weekly_summary_hour and sent_state.get("weekly") != week_key:
        title, body = build_weekly_summary(state)
        dispatch_message(config, title, body, "summary", time.time())
        append_event(state, "weekly_summary", title, body, kind="summary")
        sent_state["weekly"] = week_key
        sent.append({"title": title, "body": body})

    return sent


def collect_transitions(history: list[dict[str, Any]]) -> dict[str, list[int]]:
    plug_in: list[int] = []
    unplug: list[int] = []
    for prev, curr in zip(history, history[1:]):
        prev_mode = normalize_mode(BatterySample(**prev))
        curr_mode = normalize_mode(BatterySample(**curr))
        if prev_mode == "discharging" and curr_mode in {"charging", "charged"}:
            plug_in.append(int(prev["percent"]))
        if prev_mode in {"charging", "charged"} and curr_mode == "discharging":
            unplug.append(int(prev["percent"]))
    return {"plug_in": plug_in, "unplug": unplug}


def generate_suggestions(state: dict[str, Any], config: GuardConfig) -> list[str]:
    history = state.get("history", [])
    transitions = collect_transitions(history)
    suggestions: list[str] = []
    avg_discharge = average_rate(history, "discharging", since_hours=24 * 7)
    avg_charge = average_rate(history, "charging", since_hours=24 * 7)
    if avg_discharge is not None and avg_discharge > 14:
        suggestions.append("Recent discharge rate is fairly high. If this keeps happening, check for heavy browser/video/meeting workloads.")
    if avg_charge is not None and avg_charge < 8:
        suggestions.append("Charging looks slower than expected recently. It may be worth checking charger power or cable quality.")
    if transitions["plug_in"]:
        avg_plug = sum(transitions["plug_in"]) / len(transitions["plug_in"])
        if avg_plug <= config.lower + 2:
            suggestions.append(f"You usually plug in around {avg_plug:.0f}%. Consider topping up a bit earlier to avoid urgent low-battery moments.")
    if transitions["unplug"]:
        avg_unplug = sum(transitions["unplug"]) / len(transitions["unplug"])
        if avg_unplug >= max(85, config.upper + 5):
            suggestions.append(f"You usually unplug around {avg_unplug:.0f}%, which is above the current ceiling. If battery longevity matters, try unplugging closer to {config.upper}%." )
    weekly = summarize_period(history, state.get("events", []), 24 * 7)
    if weekly.get("anomaly_alerts", 0) >= 2:
        suggestions.append("You have had repeated anomaly-drain alerts this week. A background process or accessory may be causing extra battery drain.")
    if config.profile == "night":
        suggestions.append("Night profile is active. Quiet hours reduce late notifications, but critical low-battery alerts can still break through.")
    if config.profile == "outing":
        suggestions.append("Outing profile raises the upper limit for travel days. Remember to switch back when you no longer need the extra headroom.")
    return suggestions or ["No obvious adjustment stands out yet. Let the guard collect a bit more history for sharper recommendations."]


def dispatch_decision(state: dict[str, Any], config: GuardConfig, decision: GuardDecision) -> None:
    if not decision.notify or not decision.title or not decision.body:
        return
    dispatch_message(config, decision.title, decision.body, decision.severity, decision.sample.ts)
    if decision.notify_key:
        append_event(state, decision.notify_key, decision.title, decision.body)


def state_snapshot(state: dict[str, Any], decision: GuardDecision | None = None, config: GuardConfig | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "history_size": len(state.get("history", [])),
        "notifications": state.get("notifications", {}),
        "cycles": state.get("cycles", {}),
        "updated_at": state.get("updated_at"),
        "profile": state.get("profile"),
        "temporary_upper": state.get("temporary_upper"),
        "summary_sent": state.get("summary_sent"),
    }
    if config:
        payload["config"] = asdict(config)
    if decision:
        payload.update(
            {
                "sample": asdict(decision.sample),
                "mode": decision.mode,
                "rate_pct_per_hour": decision.rate_pct_per_hour,
                "next_check_minutes": decision.next_check_minutes,
                "notify": decision.notify,
                "notify_key": decision.notify_key,
                "title": decision.title,
                "body": decision.body,
                "severity": decision.severity,
                "debug": decision.debug,
            }
        )
    return payload


def sample_once(state: dict[str, Any], config: GuardConfig) -> GuardDecision:
    sample = read_battery()
    append_history(state, sample)
    update_cycles(state, sample)
    decision = build_decision(state=state, sample=sample, config=config)
    state["updated_at"] = sample.ts
    dispatch_decision(state, config, decision)
    maybe_send_summaries(state, config)
    return decision


def do_once(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    config = build_config(args, state)
    decision = sample_once(state, config)
    save_state(args.state_file, state)
    print(json.dumps(state_snapshot(state, decision, config), ensure_ascii=False, indent=2))
    return 0


def do_run(args: argparse.Namespace) -> int:
    while True:
        state = load_state(args.state_file)
        config = build_config(args, state)
        decision = sample_once(state, config)
        save_state(args.state_file, state)
        print(json.dumps(state_snapshot(state, decision, config), ensure_ascii=False), flush=True)
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
        ]
        + (["--profile", args.profile] if args.profile else [])
        + (["--lower", str(args.lower)] if args.lower is not None else [])
        + (["--soon", str(args.soon)] if args.soon is not None else [])
        + (["--upper", str(args.upper)] if args.upper is not None else [])
        + (["--reset-low", str(args.reset_low)] if args.reset_low is not None else [])
        + (["--reset-high", str(args.reset_high)] if args.reset_high is not None else [])
        + (["--min-interval", str(args.min_interval)] if args.min_interval is not None else [])
        + (["--max-interval", str(args.max_interval)] if args.max_interval is not None else [])
        + (["--quiet-hours", args.quiet_hours] if args.quiet_hours else [])
        + (["--daily-summary-hour", str(args.daily_summary_hour)] if args.daily_summary_hour is not None else [])
        + (["--weekly-summary-weekday", str(args.weekly_summary_weekday)] if args.weekly_summary_weekday is not None else [])
        + (["--weekly-summary-hour", str(args.weekly_summary_hour)] if args.weekly_summary_hour is not None else [])
        + (["--print-only"] if args.print_only else [])
        + (["--disable-local-notify"] if args.disable_local_notify else [])
        + (["--feishu-target", args.feishu_target] if args.feishu_target else [])
        + (["--feishu-account", args.feishu_account] if args.feishu_account else []),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")},
    }


def do_install(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    _ = build_config(args, state)
    save_state(args.state_file, state)
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
    config = build_config(args, state)
    print(json.dumps(state_snapshot(state, config=config), ensure_ascii=False, indent=2))
    return 0


def do_summary(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    config = build_config(args, state)
    title, body = build_daily_summary(state, config) if args.period == "day" else build_weekly_summary(state)
    if args.send:
        dispatch_message(config, title, body, "summary", time.time())
        append_event(state, f"manual_{args.period}_summary", title, body, kind="summary")
        save_state(args.state_file, state)
    print(json.dumps({"title": title, "body": body}, ensure_ascii=False, indent=2))
    return 0


def do_suggest(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    config = build_config(args, state)
    suggestions = generate_suggestions(state, config)
    print(json.dumps({"profile": config.profile, "suggestions": suggestions}, ensure_ascii=False, indent=2))
    return 0


def do_set_mode(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    state["profile"] = args.profile
    save_state(args.state_file, state)
    print(json.dumps({"profile": args.profile}, ensure_ascii=False, indent=2))
    return 0


def do_set_temp_upper(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    until = time.time() + args.hours * 3600
    state["temporary_upper"] = {"percent": args.percent, "until_ts": until, "until_local": iso_local(until)}
    save_state(args.state_file, state)
    print(json.dumps(state["temporary_upper"], ensure_ascii=False, indent=2))
    return 0


def do_clear_temp_upper(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    state["temporary_upper"] = None
    save_state(args.state_file, state)
    print(json.dumps({"temporary_upper": None}, ensure_ascii=False, indent=2))
    return 0


def add_shared_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-file", type=Path, default=STATE_FILE)
    parser.add_argument("--profile", choices=sorted(PROFILE_DEFAULTS))
    parser.add_argument("--lower", type=int)
    parser.add_argument("--soon", type=int)
    parser.add_argument("--upper", type=int)
    parser.add_argument("--reset-low", type=int)
    parser.add_argument("--reset-high", type=int)
    parser.add_argument("--min-interval", type=int)
    parser.add_argument("--max-interval", type=int)
    parser.add_argument("--quiet-hours")
    parser.add_argument("--daily-summary-hour", type=int)
    parser.add_argument("--weekly-summary-weekday", type=int)
    parser.add_argument("--weekly-summary-hour", type=int)
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

    status = sub.add_parser("status", help="Print the persisted state and effective config")
    add_shared_args(status)
    status.set_defaults(func=do_status)

    summary = sub.add_parser("summary", help="Generate a daily or weekly summary")
    add_shared_args(summary)
    summary.add_argument("--period", choices=["day", "week"], default="day")
    summary.add_argument("--send", action="store_true")
    summary.set_defaults(func=do_summary)

    suggest = sub.add_parser("suggest", help="Generate habit-based suggestions")
    add_shared_args(suggest)
    suggest.set_defaults(func=do_suggest)

    set_mode = sub.add_parser("set-mode", help="Persist a profile/mode")
    set_mode.add_argument("profile", choices=sorted(PROFILE_DEFAULTS))
    set_mode.add_argument("--state-file", type=Path, default=STATE_FILE)
    set_mode.set_defaults(func=do_set_mode)

    temp_upper = sub.add_parser("set-temp-upper", help="Temporarily raise or lower the charging ceiling")
    temp_upper.add_argument("percent", type=int)
    temp_upper.add_argument("--hours", type=float, default=12)
    temp_upper.add_argument("--state-file", type=Path, default=STATE_FILE)
    temp_upper.set_defaults(func=do_set_temp_upper)

    clear_temp = sub.add_parser("clear-temp-upper", help="Clear the temporary charging ceiling override")
    clear_temp.add_argument("--state-file", type=Path, default=STATE_FILE)
    clear_temp.set_defaults(func=do_clear_temp_upper)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
