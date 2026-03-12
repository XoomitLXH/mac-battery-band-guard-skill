# 🔋 mac-battery-band-guard-skill

一个给 Mac 用的电池助手，目标是尽量把电量控制在 **40%–80%**，同时减少无意义高频轮询。  
An adaptive battery assistant for Mac, designed to keep battery health in the **40%–80%** band without noisy fixed polling.

## ✨ 主要功能 / Features

- 🔌 **低电量提醒 / Low battery alerts**
  - 接近低电量时提醒充电  
    Reminds you when battery is getting low
  - 低于危险线时更频繁、更强提醒  
    Becomes more aggressive below the danger zone

- 🔋 **高电量提醒 / High battery alerts**
  - 充到上限后提醒拔电  
    Reminds you to unplug after reaching the upper limit
  - 超过上限时会重复催拔电  
    Repeats unplug reminders when battery stays too high

- ⏱ **自适应检查频率 / Adaptive checking**
  - 不固定死循环轮询  
    No dumb fixed-interval polling
  - 根据最近充放电速度动态决定下一次检查时间  
    Dynamically chooses the next check time from recent charge/discharge behavior

- 📉 **ETA 预测 / ETA prediction**
  - 预测多久会掉到低电量  
    Estimates time to low battery
  - 预测多久会充到上限  
    Estimates time to charging ceiling

- 🚨 **异常耗电检测 / Abnormal drain detection**
  - 掉电明显快于平时会提醒  
    Warns when battery drains much faster than usual

- 📝 **日报 / 周报 / Daily & weekly summaries**
  - 汇总最近电量变化  
    Summarizes recent battery behavior
  - 给出简单习惯建议  
    Gives lightweight habit suggestions

- 🎛 **模式切换 / Profiles**
  - `default`
  - `work`
  - `travel`
  - `night`
  - `auto`

- 🌙 **自动切换 / Auto switching**
  - 白天按工作模式  
    Uses work mode during the day
  - 夜里按安静模式  
    Uses quiet/night mode during quiet hours

- ✈️ **出门模式 / Trip mode**
  - 临时放宽充电上限  
    Temporarily raises the charging ceiling
  - 到时间自动恢复  
    Auto-expires after the configured time

- 💬 **提醒渠道 / Notification channels**
  - macOS 本机通知  
    Local macOS notifications
  - Feishu 推送  
    Feishu push notifications

## 🧠 风格 / Style

- 危险时：提醒更直接  
  Direct when battery is in a risky range
- 插上电后：语气更柔和一点  
  Softer tone once charging starts
- 整体尽量简洁、少打扰  
  Designed to stay simple and low-noise

## 📦 目录 / Structure

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

## 🚀 常用命令 / Common commands

```bash
# 单次检查 / One-shot check
python3 mac-battery-band-guard/scripts/battery_guard.py once --print-only

# 安装后台监控 / Install background monitor
python3 mac-battery-band-guard/scripts/battery_guard.py install-launch-agent

# 查看报告 / Show report
python3 mac-battery-band-guard/scripts/battery_guard.py report

# 切自动模式 / Enable auto mode
python3 mac-battery-band-guard/scripts/battery_guard.py set-profile auto

# 临时出门模式 / Temporary trip mode
python3 mac-battery-band-guard/scripts/battery_guard.py start-trip --hours 12 --upper 95 --set-profile-auto
```
