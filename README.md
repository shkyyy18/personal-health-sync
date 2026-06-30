# personal-health-sync

> ✨ **Found this useful? Give it a ⭐ — it helps others discover it.**

**Aggregate your scattered health data — Strava workouts + Xiaomi body-composition scale — into one local, queryable store. No cloud, no subscription, your data never leaves your machine.**

[![Stars](https://img.shields.io/github/stars/shkyyy18/personal-health-sync?style=social)](https://github.com/shkyyy18/personal-health-sync)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![platform](https://img.shields.io/badge/platform-Windows-orange.svg)](#windows-only)
[![data](https://img.shields.io/badge/data-FHIR--lite-success.svg)](https://hlth.io)

个人健康数据多源自动归集：把散落在 Strava、米家体脂秤里的数据自动拉到本地，归一成一个统一的 FHIR-lite 查询层。零云依赖、零订阅，数据全部留在本机。

> ⚠️ **Windows-only**：Strava token 通过 Windows 用户级注册表（`HKCU\Environment`）持久化与自动刷新，脚本依赖 `winreg`。macOS/Linux 需自行改造成 `.env`/keyring 方案。

---

## 为什么做

个人健康数据天然分散：运动在 Strava、体脂在米家、心率睡眠在手环 APP，每家都不提供干净的桌面端导出，手动截图 OCR 又脆又累。这个项目解决三件具体的事：

1. **Strava OAuth 全自动刷新** —— token 过期自动用 refresh_token 换新并写回，不用再每隔几天手动重授权。
2. **复用米家登录态拉体脂秤数据** —— 一次扫码登录米家 APP，凭据复用到 Mi Fitness 云，自动拉体脂秤测量值，告别截图 OCR。
3. **多源数据归一到 FHIR-lite** —— 体重/体脂/心率/步数/睡眠/运动全部归一成统一的 `Observation` 资源，一个查询入口，dashboard 或 LLM agent 可直接消费。

## 架构

```
┌──────────────┐   OAuth + 自动刷新    ┌─────────────────┐
│   Strava API │ ───────────────────▶ │ strava_sync.py  │ ──┐
└──────────────┘                      └─────────────────┘   │
                                                          │   ┌──────────────┐   ┌─────────────┐
┌──────────────┐   复用米家扫码登录态   ┌─────────────────┐   ├──▶│  本地 JSON   │ ─▶│ health_fhir │ ─▶ 统一查询
│ Mi Fitness 云│ ───────────────────▶ │mifitness_sync.py│ ──┤    │  data/*.json │   │  .py 归一层 │    latest()
└──────────────┘                      └─────────────────┘   │   └──────────────┘   └─────────────┘    history()
                                        ▲                    │                                            daily_summary()
┌──────────────┐   一次扫码登录         │                    │
│ 米家 APP 扫码│ ───────────────────▶ ┌┴────────────────┐   │
└──────────────┘                      │mijia_health_sync │ ──┘
                                      │ .py (登录态落盘)  │
                                      └─────────────────┘
```

## 数据源覆盖

| 数据源 | 自动化 | 脚本 | 说明 |
|---|---|---|---|
| Strava 运动（骑行/跑步/游泳） | ✅ 全自动 | `strava_sync.py` | OAuth 自动刷新，cron 友好 |
| 米家体脂秤（S800，含体脂/肌肉/骨盐/内脏脂肪） | ✅ 全自动 | `mifitness_sync.py` + `mijia_health_sync.py` | 复用米家登录态，无需重登录 |
| 手环心率/睡眠/血氧/压力 | ⚠️ 半自动 | `import_nxk.py`（本仓库未含） | 需手机端手动导出 `.nxk` 备份，无云端自动方案 |

> 手环数据卡在数据源：NXK 是本地备份格式，必须手机端手动导出。本仓库只发布已跑通的两条**全自动**云端路径。

## 快速开始

### 前置

```bash
git clone https://github.com/shkyyy18/personal-health-sync.git
cd personal-health-sync
pip install -r requirements.txt
```

### ① 配置 Strava

1. 在 [Strava API 设置](https://www.strava.com/settings/api) 建一个应用，拿到 `client_id` + `client_secret`。
2. 把配置模板复制到正式位置并填入 `client_id`：
   ```bash
   mkdir -p scripts/config
   cp examples/strava_config_example.json scripts/config/strava_config.json
   # 编辑 scripts/config/strava_config.json，填你的 client_id
   ```
3. 浏览器访问（替换 `client_id`）拿一次性授权 code：
   ```
   https://www.strava.com/oauth/authorize?client_id=你的ID&response_type=code&redirect_uri=http://localhost&approval_prompt=force&scope=activity:read_all
   ```
   授权后从地址栏复制 `code=` 后面那串。
4. 换 token（交互输入 code 和 client_secret，secret 不回显；成功自动写入注册表并回读校验）：
   ```bash
   python scripts/strava_set_tokens.py
   ```
5. 拉数据：
   ```bash
   python scripts/strava_sync.py 7      # 最近 7 天
   ```

### ② 配置米家体脂秤

1. 首次扫码登录米家（弹出二维码，用「米家」APP 扫）：
   ```bash
   python scripts/mijia_health_sync.py discover
   ```
   登录态落盘到 `data/.mijia/auth.json`。
2. 拉体脂数据：
   ```bash
   python scripts/mifitness_sync.py             # 最近 7 天
   python scripts/mifitness_sync.py --backfill  # 最近 90 天回填
   ```

### ③ 查询统一数据

```bash
python scripts/health_fhir.py
```
归一后的 `Observation` 落盘到 `data/fhir_observations.json`。在代码里：

```python
import sys; sys.path.insert(0, "scripts")
import health_fhir

health_fhir.latest("body-weight")          # 最新体重
health_fhir.history("body-fat-pct", 30)    # 近 30 天体脂序列
health_fhir.daily_summary()                # 今天全部指标
health_fhir.codes()                        # 所有可用指标码
```

## 安全设计

- **token 不落盘文件**：Strava 的 `client_secret` / `access_token` / `refresh_token` 全程走环境变量（Windows 注册表），代码里看不到也不写回任何 json。`strava_config.json` 只存非敏感的 `client_id`。
- **凭据文件已 gitignore**：`data/`（含体征数值）、`data/.mijia/auth.json`（米家账号级凭据）、`config/strava_config.json` 全部排除，不会进入版本库。发布前已逐文件核查无个人数据。
- **米家 auth.json 是账号级凭据**：泄露等于账号被盗，切勿外传。

## 局限

- **Windows-only**：依赖 `winreg` 持久化 token。跨平台需改造成 `.env` 或系统 keyring。
- **手环体征数据无云端自动方案**：心率/睡眠/血氧需手机端手动导出 NXK。
- **米家 API 为第三方逆向**：`python-mijiaAPI` 和 `mi-fitness-mcp` 都是社区项目，小米改接口时可能失效。
- **中文输出**：脚本 print 和代码注释为中文，国际用户需自行适配。

## 文件说明

```
scripts/
├── strava_sync.py          # Strava 拉取 + token 自动刷新（核心）
├── strava_set_tokens.py    # OAuth 一次性换 token，写入注册表
├── mifitness_sync.py       # Mi Fitness 云体脂同步
├── mijia_health_sync.py    # 米家扫码登录 + 设备发现
└── health_fhir.py          # 多源 → FHIR-lite 统一归一层
examples/
└── strava_config_example.json   # 配置模板（占位符）
```

## License

MIT — 见 [LICENSE](LICENSE)。所用第三方库（`python-mijiaAPI`、`mi-fitness-mcp`）均为各自 MIT 协议。
