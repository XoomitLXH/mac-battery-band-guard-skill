# 🔋 mac-battery-band-guard-skill

一个给 Mac 用的电池助手，目标是尽量把电量控制在 **40%–80%**，同时减少无意义高频轮询。

## ✨ 主要功能

- 🔌 **低电量提醒**
  - 接近低电量时提醒充电
  - 低于危险线时更频繁、更强提醒

- 🔋 **高电量提醒**
  - 充到上限后提醒拔电
  - 超过上限时会重复催拔电

- ⏱ **自适应检查频率**
  - 不固定死循环轮询
  - 根据最近充放电速度动态决定下一次检查时间

- 📉 **ETA 预测**
  - 预测大概多久会掉到低电量
  - 预测大概多久会充到上限

- 🚨 **异常耗电检测**
  - 掉电明显快于平时会提醒

- 📝 **日报 / 周报**
  - 汇总最近电量变化
  - 给出简单习惯建议

- 🎛 **模式切换**
  - `default`
  - `work`
  - `travel`
  - `night`
  - `auto`

- 🌙 **自动切换**
  - 白天按工作模式
  - 夜里按安静模式

- ✈️ **出门模式**
  - 临时放宽充电上限
  - 到时间自动恢复

- 💬 **提醒渠道**
  - macOS 本机通知
  - Feishu 推送

## 🧠 风格

- 危险时：提醒更直接
- 插上电后：语气更柔和一点
- 整体尽量简洁、少打扰

## 📦 目录

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

## 🚀 常用命令

```bash
# 单次检查
python3 mac-battery-band-guard/scripts/battery_guard.py once --print-only

# 安装后台监控
python3 mac-battery-band-guard/scripts/battery_guard.py install-launch-agent

# 查看报告
python3 mac-battery-band-guard/scripts/battery_guard.py report

# 切自动模式
python3 mac-battery-band-guard/scripts/battery_guard.py set-profile auto

# 临时出门模式
python3 mac-battery-band-guard/scripts/battery_guard.py start-trip --hours 12 --upper 95 --set-profile-auto
```
