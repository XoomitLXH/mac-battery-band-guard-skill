#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import plistlib
import re
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

STATE_DIR = Path.home() / "Library" / "Application Support" / "mac-battery-band-guard"
STATE_FILE = STATE_DIR / "state.json"
DEFAULT_LABEL = "ai.openclaw.mac-battery-band-guard"
DEFAULT_MIN_INTERVAL = 5
DEFAULT_MAX_INTERVAL = 180
MAX_HISTORY = 2000
MAX_HISTORY_AGE_HOURS = 24 * 30

PROFILE_PRESETS: dict[str, dict[str, Any]] = {
    "default": {
        "lower": 40,
        "soon": 45,
        "upper": 80,
        "reset_low": 50,
        "reset_high": 75,
        "quiet_hours": None,
        "summary_hour": 21,
        "weekly_summary_weekday": 6,
        "min_interval": 5,
        "max_interval": 180,
    },
    "work": {
        "lower": 40,
        "soon": 46,
        "upper": 80,
        "reset_low": 50,
        "reset_high": 75,
        "quiet_hours": None,
        "summary_hour": 19,
        "weekly_summary_weekday": 5,
        "min_interval": 5,
        "max_interval": 150,
    },
    "travel": {
        "lower": 35,
        "soon": 45,
        "upper": 95,
        "reset_low": 45,
        "reset_high": 85,
        "quiet_hours": None,
        "summary_hour": 21,
        "weekly_summary_weekday": 6,
        "min_interval": 5,
        "max_interval": 150,
    },
    "night": {
        "lower": 38,
        "soon": 43,
        "upper": 80,
        "reset_low": 48,
        "reset_high": 75,
        "quiet_hours": "23-08",
        "summary_hour": 20,
        "weekly_summary_weekday": 6,
        "min_interval": 5,
        "max_interval": 180,
    },
}
VALID_PROFILES = sorted([*PROFILE_PRESETS.keys(), "auto"])

DEFAULT_STATE = {
    "history": [],
    "notifications": {},
    "cycles": {"discharge": 0, "charge": 0},
    "last_mode": None,
    "updated_at": None,
    "settings": {
        "profile": "default",
        "quiet_hours": None,
        "summary_hour": 21,
        "weekly_summary_weekday": 6,
        "quiet_feishu_only": True,
        "auto_day_profile": "work",
        "auto_quiet_profile": "night",
    },
    "overrides": {
        "temp_upper": None,
        "temp_upper_expires_at": None,
        "travel_mode_expires_at": None,
        "travel_target_upper": None,
    },
    "summary": {
        "last_daily_date": None,
        "last_weekly_key": None,
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
class Alert:
    key: str
    title: str
    body: str
    severity: str = "normal"
    channels: str = "default"


@dataclass
class GuardDecision:
    sample: BatterySample
    mode: str
    rate_pct_per_hour: float | None
    baseline_rate_pct_per_hour: float | None
    next_check_minutes: int
    alerts: list[Alert]
    debug: dict[str, Any]
    profile: str
    effective_thresholds: dict[str, Any]


@dataclass
class RateObservation:
    rate: float
    start_ts: float
    end_ts: float
    hour: int
    weekday: int


def run_cmd(command: list[str]) -> str:
    return subprocess.check_output(command, text=True).strip()



def now_local(ts: float | None = None) -> datetime:
    return datetime.fromtimestamp(ts if ts is not None else time.time())



def iso_date(ts: float) -> str:
    return now_local(ts).date().isoformat()



def week_key(ts: float) -> str:
    dt = now_local(ts)
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"



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



def deep_merge_defaults(target: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    for key, value in defaults.items():
        if isinstance(value, dict):
            current = target.get(key)
            if not isinstance(current, dict):
                current = {}
            target[key] = deep_merge_defaults(current, value)
        else:
            target.setdefault(key, value)
    return target



def load_state(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = {}
    else:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return deep_merge_defaults(data, json.loads(json.dumps(DEFAULT_STATE)))



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
            notifications.pop("temp_upper_notice", None)
        elif mode in {"charging", "charged"}:
            cycles["charge"] = int(cycles.get("charge", 0)) + 1
            notifications.pop("charge_soon", None)
            notifications.pop("charge_now", None)
            notifications.pop("anomaly_fast_drain", None)
    state["last_mode"] = mode



def build_rate_observations(history: list[dict[str, Any]], mode: str) -> list[RateObservation]:
    observations: list[RateObservation] = []
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
        rate = delta / dt_hours
        end_dt = now_local(float(curr["ts"]))
        observations.append(
            RateObservation(
                rate=rate,
                start_ts=float(prev["ts"]),
                end_ts=float(curr["ts"]),
                hour=end_dt.hour,
                weekday=end_dt.weekday(),
            )
        )
    return observations



def weighted_recent_rate(observations: list[RateObservation]) -> float | None:
    if not observations:
        return None
    now_ts = observations[-1].end_ts
    numerator = 0.0
    denominator = 0.0
    for obs in observations:
        age_hours = max(0.0, (now_ts - obs.end_ts) / 3600)
        recency_weight = max(0.25, 1.5 - min(1.2, age_hours / 12))
        numerator += obs.rate * recency_weight
        denominator += recency_weight
    return numerator / denominator if denominator else None



def estimate_rate(history: list[dict[str, Any]], mode: str) -> float | None:
    return weighted_recent_rate(build_rate_observations(history, mode))



def estimate_baseline_rate(history: list[dict[str, Any]], mode: str, ts: float) -> float | None:
    observations = build_rate_observations(history, mode)
    if not observations:
        return None
    dt = now_local(ts)
    current_hour = dt.hour
    current_weekday = dt.weekday()

    same_hour = [obs.rate for obs in observations if abs(obs.hour - current_hour) <= 1 or abs(obs.hour - current_hour) >= 23]
    same_weekday = [obs.rate for obs in observations if obs.weekday == current_weekday]
    candidates = same_hour if len(same_hour) >= 3 else same_weekday if len(same_weekday) >= 3 else [obs.rate for obs in observations]
    if not candidates:
        return None
    return statistics.mean(candidates)



def human_duration(hours: float | None) -> str:
    if hours is None or math.isinf(hours) or math.isnan(hours):
        return "未知时间"
    minutes = max(1, round(hours * 60))
    if minutes < 60:
        return f"{minutes} 分钟"
    h, m = divmod(minutes, 60)
    if m == 0:
        return f"{h} 小时"
    return f"{h} 小时 {m} 分钟"



def human_rate(rate: float | None) -> str:
    if rate is None or math.isinf(rate) or math.isnan(rate):
        return "未知"
    return f"{abs(rate):.1f}%/小时"



def parse_quiet_hours(raw: str | None) -> tuple[int, int] | None:
    if not raw:
        return None
    match = re.fullmatch(r"(\d{1,2})-(\d{1,2})", raw.strip())
    if not match:
        return None
    start = int(match.group(1)) % 24
    end = int(match.group(2)) % 24
    return start, end



def in_quiet_hours(ts: float, quiet_hours: str | None) -> bool:
    parsed = parse_quiet_hours(quiet_hours)
    if parsed is None:
        return False
    start, end = parsed
    hour = now_local(ts).hour
    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end



def choose_interval(
    sample: BatterySample,
    rate: float | None,
    lower: int,
    upper: int,
    min_minutes: int,
    max_minutes: int,
) -> tuple[int, dict[str, Any]]:
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



def configured_profile(args: argparse.Namespace, state: dict[str, Any]) -> str:
    profile = state.get("settings", {}).get("profile") or args.profile
    return profile if profile in VALID_PROFILES else "default"



def resolve_active_profile(args: argparse.Namespace, state: dict[str, Any], ts: float) -> tuple[str, str]:
    profile = configured_profile(args, state)
    travel = active_travel_mode(state, ts)
    if travel is not None:
        return "travel", "temporary-travel"
    if profile != "auto":
        return profile, "configured"

    settings = state.get("settings", {})
    quiet_hours = settings.get("quiet_hours") or PROFILE_PRESETS["night"].get("quiet_hours")
    day_profile = settings.get("auto_day_profile") or "work"
    quiet_profile = settings.get("auto_quiet_profile") or "night"
    if day_profile not in PROFILE_PRESETS:
        day_profile = "work"
    if quiet_profile not in PROFILE_PRESETS:
        quiet_profile = "night"

    if in_quiet_hours(ts, quiet_hours):
        return quiet_profile, "auto-quiet"
    return day_profile, "auto-day"



def active_temp_upper(state: dict[str, Any], now_ts: float) -> int | None:
    overrides = state.setdefault("overrides", {})
    value = overrides.get("temp_upper")
    expires_at = overrides.get("temp_upper_expires_at")
    if value is None:
        return None
    if expires_at is not None and float(expires_at) <= now_ts:
        overrides["temp_upper"] = None
        overrides["temp_upper_expires_at"] = None
        return None
    return int(value)



def active_travel_mode(state: dict[str, Any], now_ts: float) -> dict[str, Any] | None:
    overrides = state.setdefault("overrides", {})
    expires_at = overrides.get("travel_mode_expires_at")
    if expires_at is None:
        return None
    if float(expires_at) <= now_ts:
        overrides["travel_mode_expires_at"] = None
        overrides["travel_target_upper"] = None
        return None
    return {
        "expires_at": float(expires_at),
        "target_upper": overrides.get("travel_target_upper"),
    }



def effective_settings(args: argparse.Namespace, state: dict[str, Any], sample: BatterySample) -> dict[str, Any]:
    selected_profile, profile_source = resolve_active_profile(args, state, sample.ts)
    configured = configured_profile(args, state)
    preset = PROFILE_PRESETS[selected_profile].copy()
    settings = state.setdefault("settings", {})
    quiet_hours = settings.get("quiet_hours")
    summary_hour = settings.get("summary_hour")
    weekly_summary_weekday = settings.get("weekly_summary_weekday")
    quiet_feishu_only = settings.get("quiet_feishu_only", True)

    result = {
        "profile": selected_profile,
        "configured_profile": configured,
        "profile_source": profile_source,
        "lower": int(preset["lower"]),
        "soon": int(preset["soon"]),
        "upper": int(preset["upper"]),
        "reset_low": int(preset["reset_low"]),
        "reset_high": int(preset["reset_high"]),
        "min_interval": int(preset["min_interval"]),
        "max_interval": int(preset["max_interval"]),
        "quiet_hours": quiet_hours if quiet_hours is not None else preset.get("quiet_hours"),
        "summary_hour": int(summary_hour if summary_hour is not None else preset.get("summary_hour", 21)),
        "weekly_summary_weekday": int(
            weekly_summary_weekday if weekly_summary_weekday is not None else preset.get("weekly_summary_weekday", 6)
        ),
        "quiet_feishu_only": bool(quiet_feishu_only),
        "auto_day_profile": settings.get("auto_day_profile", "work"),
        "auto_quiet_profile": settings.get("auto_quiet_profile", "night"),
    }

    temp_upper = active_temp_upper(state, sample.ts)
    travel = active_travel_mode(state, sample.ts)
    if travel and travel.get("target_upper"):
        temp_upper = max(int(travel["target_upper"]), temp_upper or 0)

    if temp_upper is not None and temp_upper > result["upper"]:
        result["upper"] = temp_upper
        result["temp_upper_active"] = True
    else:
        result["temp_upper_active"] = False

    result["travel_mode_active"] = travel is not None
    result["travel_mode_expires_at"] = travel.get("expires_at") if travel else None

    if selected_profile == "default":
        # Keep explicit CLI thresholds as the default-profile fallback.
        result["lower"] = args.lower
        result["soon"] = args.soon
        result["upper"] = max(result["upper"], args.upper)
        result["reset_low"] = args.reset_low
        result["reset_high"] = args.reset_high
        result["min_interval"] = args.min_interval
        result["max_interval"] = args.max_interval

    return result



def format_charge_alert(sample: BatterySample, lower: int, soon: int, rate: float | None, eta: float | None, anomaly: bool) -> tuple[str, str]:
    eta_text = human_duration(eta)
    pace_text = human_rate(rate)
    if sample.percent <= lower:
        title = "Battery Guard · 现在该充电了"
        body = f"电量 {sample.percent}%，已经低于 {lower}% 下限。按现在速度（约 {pace_text}）继续掉的话会更快逼近危险区，建议现在插电。"
    else:
        title = "Battery Guard · 快该充电了"
        body = f"电量 {sample.percent}%，离 {lower}% 还不远，按现在速度大约 {eta_text} 后会到下限。"
        body += " 这次提醒是提前量提醒，给你留一点缓冲。"
    if anomaly:
        body += " 另外今天掉电明显比平时快，建议顺手检查高负载应用。"
    return title, body



def format_stop_alert(sample: BatterySample, upper: int, rate: float | None, temp_upper_active: bool) -> tuple[str, str]:
    title = "Battery Guard · 可以停止充电了"
    body = f"电量已经到 {sample.percent}%，达到 {upper}% 上限。现在拔电最合适，可以少一些高电量停留。"
    if temp_upper_active:
        body += " 当前使用的是临时放宽上限模式。"
    elif rate is not None and rate > 0:
        body += f" 最近充电速度大约 {human_rate(rate)}。"
    return title, body



def detect_fast_drain_alert(
    state: dict[str, Any],
    sample: BatterySample,
    rate: float | None,
    baseline_rate: float | None,
    soon: int,
) -> Alert | None:
    if normalize_mode(sample) != "discharging":
        return None
    if rate is None or baseline_rate is None or rate >= 0 or baseline_rate >= 0:
        return None
    if sample.percent <= soon:
        return None

    abs_rate = abs(rate)
    abs_baseline = max(0.1, abs(baseline_rate))
    ratio = abs_rate / abs_baseline
    if abs_rate < 10 or ratio < 1.7:
        return None

    cycle = int(state.get("cycles", {}).get("discharge", 0))
    last_cycle = state.setdefault("notifications", {}).get("anomaly_fast_drain", {}).get("cycle")
    if last_cycle == cycle:
        return None
    state["notifications"]["anomaly_fast_drain"] = {"cycle": cycle, "ts": sample.ts}

    title = "Battery Guard · 今天掉电有点异常"
    body = (
        f"当前掉电速度约 {human_rate(rate)}，比你这类时段的常见速度快了约 {ratio:.1f} 倍。"
        " 如果你没在做重负载任务，建议看看浏览器标签页、会议软件或后台进程。"
    )
    return Alert(key="anomaly_fast_drain", title=title, body=body, severity="high")



def maybe_threshold_alerts(
    state: dict[str, Any],
    sample: BatterySample,
    rate: float | None,
    lower: int,
    soon: int,
    upper: int,
    reset_low: int,
    reset_high: int,
    temp_upper_active: bool,
    anomaly: bool,
) -> list[Alert]:
    alerts: list[Alert] = []
    notifications = state.setdefault("notifications", {})
    cycles = state.setdefault("cycles", {"discharge": 0, "charge": 0})
    mode = normalize_mode(sample)

    if mode in {"charging", "charged"} and sample.percent >= reset_low:
        notifications.pop("charge_soon", None)
        notifications.pop("charge_now", None)
    if mode == "discharging" and sample.percent < reset_high:
        notifications.pop("stop_at_upper", None)
        notifications.pop("temp_upper_notice", None)

    if mode == "discharging":
        eta = None
        if rate is not None and rate < 0 and sample.percent > lower:
            eta = (sample.percent - lower) / abs(rate)
        cycle = int(cycles.get("discharge", 0))

        if sample.percent <= lower:
            last_cycle = notifications.get("charge_now", {}).get("cycle")
            if last_cycle != cycle:
                notifications["charge_now"] = {"cycle": cycle, "ts": sample.ts}
                title, body = format_charge_alert(sample, lower, soon, rate, eta, anomaly)
                alerts.append(Alert(key="charge_now", title=title, body=body, severity="critical"))
        elif sample.percent <= soon:
            last_cycle = notifications.get("charge_soon", {}).get("cycle")
            if last_cycle != cycle:
                notifications["charge_soon"] = {"cycle": cycle, "ts": sample.ts}
                title, body = format_charge_alert(sample, lower, soon, rate, eta, anomaly)
                alerts.append(Alert(key="charge_soon", title=title, body=body, severity="normal"))

    if mode in {"charging", "charged"} and sample.percent >= upper:
        cycle = int(cycles.get("charge", 0))
        last_cycle = notifications.get("stop_at_upper", {}).get("cycle")
        if last_cycle != cycle:
            notifications["stop_at_upper"] = {"cycle": cycle, "ts": sample.ts}
            title, body = format_stop_alert(sample, upper, rate, temp_upper_active)
            alerts.append(Alert(key="stop_at_upper", title=title, body=body, severity="normal"))

    return alerts



def summarize_window(history: list[dict[str, Any]], start_ts: float, end_ts: float, upper: int) -> dict[str, Any]:
    window = [item for item in history if start_ts <= float(item.get("ts", 0)) <= end_ts]
    if not window:
        return {
            "samples": 0,
            "min_percent": None,
            "max_percent": None,
            "avg_percent": None,
            "time_above_upper_hours": 0.0,
            "charge_rate": None,
            "discharge_rate": None,
        }

    percents = [int(item["percent"]) for item in window]
    time_above_upper = 0.0
    for prev, curr in zip(window, window[1:]):
        if int(prev["percent"]) >= upper:
            dt = max(0.0, (float(curr["ts"]) - float(prev["ts"])))
            time_above_upper += dt / 3600

    discharge_rate = estimate_rate(window, "discharging")
    charge_rate = estimate_rate(window, "charging")
    return {
        "samples": len(window),
        "min_percent": min(percents),
        "max_percent": max(percents),
        "avg_percent": round(statistics.mean(percents), 1),
        "time_above_upper_hours": round(time_above_upper, 2),
        "charge_rate": charge_rate,
        "discharge_rate": discharge_rate,
    }



def build_learning_insights(history: list[dict[str, Any]], upper: int, now_ts: float) -> list[str]:
    if len(history) < 6:
        return ["历史数据还不够，先再跑几天，建议会更准。"]

    seven_days_ago = now_ts - 7 * 24 * 3600
    recent = [item for item in history if float(item.get("ts", 0)) >= seven_days_ago]
    if len(recent) < 4:
        return ["最近 7 天数据偏少，还不足以做稳定习惯判断。"]

    insights: list[str] = []
    discharge_rate = estimate_rate(recent, "discharging")
    charge_rate = estimate_rate(recent, "charging")
    if discharge_rate is not None:
        insights.append(f"最近一周平均掉电速度大约 {human_rate(discharge_rate)}。")
    if charge_rate is not None:
        insights.append(f"最近一周平均充电速度大约 {human_rate(charge_rate)}。")

    high_charge_samples = [item for item in recent if int(item["percent"]) >= upper]
    if high_charge_samples and len(high_charge_samples) / len(recent) > 0.18:
        insights.append("你最近让电量停留在高电量区的时间偏多，可以考虑更早拔电。")

    night_charge_samples = [
        item for item in recent
        if normalize_mode(BatterySample(**item)) in {"charging", "charged"}
        and (now_local(float(item["ts"])).hour >= 23 or now_local(float(item["ts"])).hour < 7)
    ]
    if len(night_charge_samples) >= max(3, len(recent) // 8):
        insights.append("你有比较明显的夜间充电习惯，如果想更保守一点，可以切到 night 模式。")

    top_levels = [int(item["percent"]) for item in recent if normalize_mode(BatterySample(**item)) in {"charging", "charged"}]
    if top_levels and statistics.mean(top_levels) > 86:
        insights.append("最近充到的平均高点偏高；如果不是出门场景，没必要总是冲太满。")

    if not insights:
        insights.append("最近的充放电习惯整体还算稳，没有特别明显的问题。")
    return insights[:4]



def maybe_summary_alerts(
    state: dict[str, Any],
    sample: BatterySample,
    history: list[dict[str, Any]],
    summary_hour: int,
    weekly_summary_weekday: int,
    upper: int,
) -> list[Alert]:
    alerts: list[Alert] = []
    summary_state = state.setdefault("summary", {})
    dt = now_local(sample.ts)
    today_key = dt.date().isoformat()
    current_week_key = week_key(sample.ts)

    if dt.hour >= summary_hour and summary_state.get("last_daily_date") != today_key:
        start_of_day = datetime(dt.year, dt.month, dt.day).timestamp()
        daily = summarize_window(history, start_of_day, sample.ts, upper)
        if daily["samples"] >= 2:
            summary_state["last_daily_date"] = today_key
            body = (
                f"今天最低 {daily['min_percent']}%，最高 {daily['max_percent']}%，平均 {daily['avg_percent']}%。"
                f" 高电量区停留约 {daily['time_above_upper_hours']} 小时。"
            )
            if daily["discharge_rate"] is not None:
                body += f" 平均掉电速度约 {human_rate(daily['discharge_rate'])}。"
            alerts.append(Alert(key="daily_summary", title="Battery Guard · 今日电量摘要", body=body, severity="info", channels="feishu_only"))

    if (
        dt.hour >= summary_hour
        and dt.weekday() == weekly_summary_weekday
        and summary_state.get("last_weekly_key") != current_week_key
    ):
        start_ts = sample.ts - 7 * 24 * 3600
        weekly = summarize_window(history, start_ts, sample.ts, upper)
        insights = build_learning_insights(history, upper, sample.ts)
        if weekly["samples"] >= 4:
            summary_state["last_weekly_key"] = current_week_key
            insight_text = " ".join(insights)
            body = (
                f"过去 7 天最低 {weekly['min_percent']}%，最高 {weekly['max_percent']}%，平均 {weekly['avg_percent']}%。"
                f" 高电量区停留约 {weekly['time_above_upper_hours']} 小时。 {insight_text}"
            )
            alerts.append(Alert(key="weekly_summary", title="Battery Guard · 本周电池总结", body=body, severity="info", channels="feishu_only"))

    return alerts



def apply_quiet_hours(alerts: list[Alert], ts: float, quiet_hours: str | None, quiet_feishu_only: bool) -> list[Alert]:
    if not in_quiet_hours(ts, quiet_hours):
        return alerts
    adjusted: list[Alert] = []
    for alert in alerts:
        if alert.severity in {"critical", "high"}:
            adjusted.append(alert)
        elif quiet_feishu_only:
            adjusted.append(Alert(key=alert.key, title=alert.title, body=alert.body, severity=alert.severity, channels="feishu_only"))
    return adjusted



def build_decision(state: dict[str, Any], sample: BatterySample, args: argparse.Namespace) -> GuardDecision:
    history = state.get("history", [])
    mode = normalize_mode(sample)
    settings = effective_settings(args, state, sample)
    rate = estimate_rate(history, mode)
    baseline_rate = estimate_baseline_rate(history, mode, sample.ts)
    next_check_minutes, debug = choose_interval(
        sample,
        rate,
        settings["lower"],
        settings["upper"],
        settings["min_interval"],
        settings["max_interval"],
    )
    fast_drain_alert = detect_fast_drain_alert(state, sample, rate, baseline_rate, settings["soon"])
    threshold_alerts = maybe_threshold_alerts(
        state=state,
        sample=sample,
        rate=rate,
        lower=settings["lower"],
        soon=settings["soon"],
        upper=settings["upper"],
        reset_low=settings["reset_low"],
        reset_high=settings["reset_high"],
        temp_upper_active=bool(settings.get("temp_upper_active")),
        anomaly=fast_drain_alert is not None,
    )
    summary_alerts = maybe_summary_alerts(
        state=state,
        sample=sample,
        history=history,
        summary_hour=settings["summary_hour"],
        weekly_summary_weekday=settings["weekly_summary_weekday"],
        upper=settings["upper"],
    )

    alerts = []
    if fast_drain_alert:
        alerts.append(fast_drain_alert)
    alerts.extend(threshold_alerts)
    alerts.extend(summary_alerts)
    alerts = apply_quiet_hours(alerts, sample.ts, settings.get("quiet_hours"), settings.get("quiet_feishu_only", True))

    debug.update(
        {
            "baseline_rate_pct_per_hour": baseline_rate,
            "profile": settings["profile"],
            "configured_profile": settings.get("configured_profile"),
            "profile_source": settings.get("profile_source"),
            "quiet_hours": settings.get("quiet_hours"),
            "temp_upper_active": settings.get("temp_upper_active", False),
        }
    )
    return GuardDecision(
        sample=sample,
        mode=mode,
        rate_pct_per_hour=rate,
        baseline_rate_pct_per_hour=baseline_rate,
        next_check_minutes=next_check_minutes,
        alerts=alerts,
        debug=debug,
        profile=settings["profile"],
        effective_thresholds=settings,
    )



def send_notification(title: str, body: str) -> None:
    script = (
        f'display notification {json.dumps(body, ensure_ascii=False)} '
        f'with title {json.dumps(title, ensure_ascii=False)}'
    )
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
    if args.print_only:
        return
    for alert in decision.alerts:
        send_local = not args.disable_local_notify and alert.channels != "feishu_only"
        send_feishu = bool(args.feishu_target)
        if send_local:
            send_notification(alert.title, alert.body)
        if send_feishu:
            send_feishu_message(
                target=args.feishu_target,
                title=alert.title,
                body=alert.body,
                account=args.feishu_account,
            )



def state_snapshot(state: dict[str, Any], decision: GuardDecision) -> dict[str, Any]:
    return {
        "sample": asdict(decision.sample),
        "mode": decision.mode,
        "profile": decision.profile,
        "rate_pct_per_hour": decision.rate_pct_per_hour,
        "baseline_rate_pct_per_hour": decision.baseline_rate_pct_per_hour,
        "next_check_minutes": decision.next_check_minutes,
        "alerts": [asdict(alert) for alert in decision.alerts],
        "effective_thresholds": decision.effective_thresholds,
        "debug": decision.debug,
        "history_size": len(state.get("history", [])),
        "notifications": state.get("notifications", {}),
        "cycles": state.get("cycles", {}),
        "summary": state.get("summary", {}),
        "settings": state.get("settings", {}),
        "overrides": state.get("overrides", {}),
        "updated_at": state.get("updated_at"),
    }



def do_sample(args: argparse.Namespace, notify: bool) -> int:
    state = load_state(args.state_file)
    sample = read_battery()
    append_history(state, sample)
    update_cycles(state, sample)
    decision = build_decision(state=state, sample=sample, args=args)
    state["updated_at"] = sample.ts
    save_state(args.state_file, state)
    if notify:
        dispatch_notifications(args, decision)
    print(json.dumps(state_snapshot(state, decision), ensure_ascii=False, indent=2))
    return 0



def do_once(args: argparse.Namespace) -> int:
    return do_sample(args, notify=True)



def do_run(args: argparse.Namespace) -> int:
    while True:
        state = load_state(args.state_file)
        sample = read_battery()
        append_history(state, sample)
        update_cycles(state, sample)
        decision = build_decision(state=state, sample=sample, args=args)
        state["updated_at"] = sample.ts
        save_state(args.state_file, state)
        print(json.dumps(state_snapshot(state, decision), ensure_ascii=False), flush=True)
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
            "--profile",
            str(args.profile),
            "--summary-hour",
            str(args.summary_hour),
            "--weekly-summary-weekday",
            str(args.weekly_summary_weekday),
        ]
        + (["--print-only"] if args.print_only else [])
        + (["--disable-local-notify"] if args.disable_local_notify else [])
        + (["--feishu-target", args.feishu_target] if args.feishu_target else [])
        + (["--feishu-account", args.feishu_account] if args.feishu_account else [])
        + (["--quiet-hours", args.quiet_hours] if args.quiet_hours else []),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        },
    }



def do_install(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    settings = state.setdefault("settings", {})
    settings["profile"] = args.profile
    settings["summary_hour"] = args.summary_hour
    settings["weekly_summary_weekday"] = args.weekly_summary_weekday
    if args.quiet_hours is not None:
        settings["quiet_hours"] = args.quiet_hours
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
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0



def do_report(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    history = state.get("history", [])
    now_ts = time.time()
    sample = read_battery()
    settings = effective_settings(args, state, sample)
    today_start = datetime(now_local().year, now_local().month, now_local().day).timestamp()
    report = {
        "configured_profile": configured_profile(args, state),
        "active_profile": settings["profile"],
        "profile_source": settings.get("profile_source"),
        "current_sample": asdict(sample),
        "today": summarize_window(history, today_start, now_ts, settings["upper"]),
        "week": summarize_window(history, now_ts - 7 * 24 * 3600, now_ts, settings["upper"]),
        "insights": build_learning_insights(history, settings["upper"], now_ts),
        "overrides": state.get("overrides", {}),
        "settings": state.get("settings", {}),
        "effective_settings": settings,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0



def do_set_profile(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    if args.profile not in VALID_PROFILES:
        raise SystemExit(f"Unknown profile: {args.profile}")
    settings = state.setdefault("settings", {})
    settings["profile"] = args.profile
    if args.profile in PROFILE_PRESETS:
        preset = PROFILE_PRESETS[args.profile]
        settings["quiet_hours"] = preset.get("quiet_hours")
        settings["summary_hour"] = preset.get("summary_hour", settings.get("summary_hour", 21))
        settings["weekly_summary_weekday"] = preset.get(
            "weekly_summary_weekday", settings.get("weekly_summary_weekday", 6)
        )
    else:
        settings.setdefault("auto_day_profile", "work")
        settings.setdefault("auto_quiet_profile", "night")
        if settings.get("quiet_hours") is None:
            settings["quiet_hours"] = PROFILE_PRESETS["night"].get("quiet_hours")
    save_state(args.state_file, state)
    print(json.dumps({"ok": True, "profile": args.profile, "settings": settings}, ensure_ascii=False))
    return 0



def do_set_quiet_hours(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    state.setdefault("settings", {})["quiet_hours"] = args.quiet_hours
    save_state(args.state_file, state)
    print(json.dumps({"ok": True, "quiet_hours": args.quiet_hours}, ensure_ascii=False))
    return 0



def do_set_auto_profiles(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    settings = state.setdefault("settings", {})
    settings["auto_day_profile"] = args.day_profile
    settings["auto_quiet_profile"] = args.quiet_profile
    save_state(args.state_file, state)
    print(
        json.dumps(
            {
                "ok": True,
                "auto_day_profile": args.day_profile,
                "auto_quiet_profile": args.quiet_profile,
            },
            ensure_ascii=False,
        )
    )
    return 0



def do_set_temp_upper(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    overrides = state.setdefault("overrides", {})
    overrides["temp_upper"] = args.value
    expires_at = None
    if args.hours is not None:
        expires_at = time.time() + args.hours * 3600
    elif args.until:
        expires_at = datetime.fromisoformat(args.until).timestamp()
    overrides["temp_upper_expires_at"] = expires_at
    save_state(args.state_file, state)
    print(json.dumps({"ok": True, "temp_upper": args.value, "expires_at": expires_at}, ensure_ascii=False))
    return 0



def do_clear_temp_upper(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    overrides = state.setdefault("overrides", {})
    overrides["temp_upper"] = None
    overrides["temp_upper_expires_at"] = None
    save_state(args.state_file, state)
    print(json.dumps({"ok": True}, ensure_ascii=False))
    return 0



def do_start_trip(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    overrides = state.setdefault("overrides", {})
    overrides["travel_mode_expires_at"] = time.time() + (args.hours * 3600)
    overrides["travel_target_upper"] = args.upper
    if args.set_profile_auto:
        state.setdefault("settings", {})["profile"] = "auto"
    save_state(args.state_file, state)
    print(
        json.dumps(
            {
                "ok": True,
                "travel_mode_expires_at": overrides["travel_mode_expires_at"],
                "travel_target_upper": args.upper,
                "profile": state.get("settings", {}).get("profile"),
            },
            ensure_ascii=False,
        )
    )
    return 0



def do_end_trip(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    overrides = state.setdefault("overrides", {})
    overrides["travel_mode_expires_at"] = None
    overrides["travel_target_upper"] = None
    save_state(args.state_file, state)
    print(json.dumps({"ok": True}, ensure_ascii=False))
    return 0



def do_test_alert(args: argparse.Namespace) -> int:
    sample = read_battery()
    title = "Battery Guard · 测试提醒"
    body = f"这是一次测试提醒。当前电量 {sample.percent}%，模式为 {normalize_mode(sample)}。"
    alert = Alert(key="test_alert", title=title, body=body, severity="info")
    decision = GuardDecision(
        sample=sample,
        mode=normalize_mode(sample),
        rate_pct_per_hour=None,
        baseline_rate_pct_per_hour=None,
        next_check_minutes=0,
        alerts=[alert],
        debug={"test": True},
        profile="test",
        effective_thresholds={},
    )
    dispatch_notifications(args, decision)
    print(json.dumps({"ok": True, "title": title, "body": body}, ensure_ascii=False))
    return 0



def add_shared_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-file", type=Path, default=STATE_FILE)
    parser.add_argument("--lower", type=int, default=40)
    parser.add_argument("--soon", type=int, default=45)
    parser.add_argument("--upper", type=int, default=80)
    parser.add_argument("--reset-low", type=int, default=50)
    parser.add_argument("--reset-high", type=int, default=75)
    parser.add_argument("--min-interval", type=int, default=DEFAULT_MIN_INTERVAL)
    parser.add_argument("--max-interval", type=int, default=DEFAULT_MAX_INTERVAL)
    parser.add_argument("--profile", default="default", choices=VALID_PROFILES)
    parser.add_argument("--quiet-hours")
    parser.add_argument("--summary-hour", type=int, default=21)
    parser.add_argument("--weekly-summary-weekday", type=int, default=6)
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

    status = sub.add_parser("status", help="Print the persisted state JSON")
    status.add_argument("--state-file", type=Path, default=STATE_FILE)
    status.set_defaults(func=do_status)

    report = sub.add_parser("report", help="Print learned insights and summary metrics")
    add_shared_args(report)
    report.set_defaults(func=do_report)

    set_profile = sub.add_parser("set-profile", help="Switch the active profile")
    set_profile.add_argument("profile", choices=VALID_PROFILES)
    set_profile.add_argument("--state-file", type=Path, default=STATE_FILE)
    set_profile.set_defaults(func=do_set_profile)

    set_quiet = sub.add_parser("set-quiet-hours", help="Set quiet hours like 23-08")
    set_quiet.add_argument("quiet_hours")
    set_quiet.add_argument("--state-file", type=Path, default=STATE_FILE)
    set_quiet.set_defaults(func=do_set_quiet_hours)

    set_auto_profiles = sub.add_parser("set-auto-profiles", help="Choose which profiles auto mode uses for day and quiet hours")
    set_auto_profiles.add_argument("--day-profile", required=True, choices=sorted(PROFILE_PRESETS.keys()))
    set_auto_profiles.add_argument("--quiet-profile", required=True, choices=sorted(PROFILE_PRESETS.keys()))
    set_auto_profiles.add_argument("--state-file", type=Path, default=STATE_FILE)
    set_auto_profiles.set_defaults(func=do_set_auto_profiles)

    temp_upper = sub.add_parser("set-temp-upper", help="Temporarily raise the upper threshold")
    temp_upper.add_argument("value", type=int)
    temp_upper.add_argument("--hours", type=float)
    temp_upper.add_argument("--until")
    temp_upper.add_argument("--state-file", type=Path, default=STATE_FILE)
    temp_upper.set_defaults(func=do_set_temp_upper)

    clear_temp = sub.add_parser("clear-temp-upper", help="Clear temporary upper-threshold override")
    clear_temp.add_argument("--state-file", type=Path, default=STATE_FILE)
    clear_temp.set_defaults(func=do_clear_temp_upper)

    start_trip = sub.add_parser("start-trip", help="Temporarily enable travel behavior and higher charge ceiling")
    start_trip.add_argument("--hours", type=float, default=12)
    start_trip.add_argument("--upper", type=int, default=95)
    start_trip.add_argument("--set-profile-auto", action="store_true")
    start_trip.add_argument("--state-file", type=Path, default=STATE_FILE)
    start_trip.set_defaults(func=do_start_trip)

    end_trip = sub.add_parser("end-trip", help="End temporary travel behavior immediately")
    end_trip.add_argument("--state-file", type=Path, default=STATE_FILE)
    end_trip.set_defaults(func=do_end_trip)

    test_alert = sub.add_parser("test-alert", help="Send a test notification through configured channels")
    add_shared_args(test_alert)
    test_alert.set_defaults(func=do_test_alert)

    return parser



def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
