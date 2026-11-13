# Garmin 健康趋势看板 · 睡眠 / HRV / 卡路里 + DeepSeek 解读

一个**本地运行**的健康数据分析看板：趋势数据来自 **Garmin Connect Personal API（官方只读）**，趋势解读由 **DeepSeek 大模型**生成。零第三方 Python 依赖，只用标准库。

- 三张趋势图：睡眠结构（深睡/浅睡/REM/清醒）+ 评分折线、HRV（个人基线带 + 7 日均线）、卡路里构成（基础代谢 + 活动消耗 + 均线）
- 4 个 KPI（近 7 日均值 + 环比）、异常日、指标间相关性、AI 四段解读
- **无凭证也能完整运行**：自动切换到内置 90 天演示数据；直接用 `file://` 打开 `web/index.html` 也能看（内置离线数据）
- 凭证只留在本机，不进日志、不进前端响应、不进 Git

---

## 1. 快速开始

### 1.1 双击桌面图标（推荐）

桌面快捷方式 **`Garmin 健康看板`**（自定义紫色心电图标）：

1. 双击图标 → 自动启动本地服务、等待端口就绪、打开浏览器 `http://127.0.0.1:8765`。
2. 服务运行在一个标题为 `Garmin Health Dashboard - close this window to stop` 的控制台窗口里，**关掉该窗口即停止服务**。
3. 重复双击不会重复起服务：端口已占用时直接打开浏览器复用现有服务。
4. 点击页面右上角 **「⚙ 设置」**，在网页里填 API Key、切换中国区 / 全球区，点「保存并刷新数据」**立即生效，无需重启服务**。

> 首次使用也可以什么都不填，直接看 90 天演示数据；配置好 Key 后再保存即可切到真实数据。

**手工启动 / 重装后重建图标**

```bash
python server.py                 # 手工启动（Python 3.9+，无需 pip install）
python launcher.py               # 等价于双击桌面图标（会打开浏览器）
python launcher.py --stop        # 停止由 launcher 启动的服务
python create_shortcut.py        # 重建桌面快捷方式（需要 pywin32，仅此步骤）
python make_icon.py              # 重新生成图标（需要 Pillow，仅此步骤）
```

备用入口 `start_dashboard.vbs` / `stop_dashboard.vbs` 也可双击；它们调用同一个 `launcher.py`。

### 1.2 网页设置面板

右上角「⚙ 设置」里可以修改的内容（**保存后立即生效并自动重新拉取数据**）：

| 分组 | 可改项 |
|---|---|
| Garmin | Personal API Key、**API 地区（全球区 / 中国区）**、自定义 API 主机（高级） |
| DeepSeek | API Key、Base URL、模型（`deepseek-flash` / `deepseek-v4-pro`）、思考模式 |
| 运行参数 | 数据源 `auto / garmin / mock`、默认时间范围、缓存时长、请求超时 |

- 密钥框**只显示 `****abcd` 尾四位提示，不回显明文**；留空 = 保持原值，点「清除」= 删除已保存的 Key。
- 切换地区会自动丢弃旧的自定义主机，主机提示实时显示当前生效地址；获取 Key 的链接也随地区切换。
- 保存后服务端会清除旧的 token / 数据 / 解读缓存，避免不同地区的缓存数据混用。
- 弹窗内「测试 Garmin」「测试 DeepSeek」分别验证两边连通性（DeepSeek 测试只发 1 token 的极短请求）；有未保存改动时会提示先保存。
- **唯一需要重启的是 `PORT`**：它是服务启动参数，改 `.env` 后下次启动生效。

看板右上角「测试连接」可一键验证 Garmin token 交换与只读访问；「查看原始响应」能核对 API 返回的真实字段名。

---

## 2. 接口调研结论（截至 2026-11-12）

### 2.1 结论：现在有官方个人 API，不用再折腾模拟登录

| 时间 | 变化 |
|---|---|
| 2026-03 | Garmin 因 AI 生成应用申请暴增，冻结 Connect Developer Program 新申请 |
| **2026-10-21** | **重新开放**，把原来单一的企业门槛拆成 Personal / Starter / Enterprise 三级 |
| 现在 | **Personal 层级个人免费、免审批、几秒钟自助生成 Key**，专门给"读取自己的数据做个人应用"用 |

所以本看板走**官方 Personal API**，不使用 `python-garminconnect` / `garth` 这类模拟网页登录的非官方库，也不调用任何私有接口。

### 2.2 三级权限对比

| 层级 | 对象 | 审批 | 费用 | 能力 |
|---|---|---|---|---|
| **Personal** | 个人读自己的数据 | 无需，自助生成 Key | 免费 | Activity / Health / Women's Health 的 JSON 摘要 + 完整 .FIT 文件，**仅 pull、仅只读** |
| Starter | ≤1000 用户的组织 | 近乎即时 | 免费 | 上面全部 + Courses / Training API（可推送路线与训练计划） |
| Enterprise | >1000 用户 | 申请 | $1,000 入门费（抵月费）+ 按 MAU 计费，2027-04-01 起 | 上面全部 + Golf API、历史数据回填、技术支持 |

> Garmin Health SDK 仍是另一套面向企业/机构的方案（需商业授权），与 Personal API 不是一回事；Connect IQ 是手表应用平台，也与本看板无关。

### 2.3 Personal API 的关键约束（都已在代码里处理）

| 约束 | 说明 | 本看板对策 |
|---|---|---|
| 认证是两步 | Key **不是** bearer token。先 POST 换 access token，业务请求再用 Bearer | `garmin_client.py` 自动换取 + 本地缓存 |
| **token 端点不接受 JSON 协商** | 官方返回**纯文本 JWT**（不是 JSON）。带 `Accept: application/json` 请求 token 会拿到 **HTTP 406 Not Acceptable**；官方 FAQ 的 curl 示例不带任何 Accept 头 | token 请求完全不发 Accept；所有请求遇 406 自动改用 `Accept: */*` 重试一次；认证失败直接中止，不做逐日降级 |
| token 24 小时 | 无 refresh token；token 与 Key 谁先到期取先到者 | 解析 JWT `exp`，23 小时保守上限，过期自动重换；401 自动重换并重试一次 |
| Key 只显示一次 | 有效期 30/90/180/365 天，可随时撤销，到期前一周邮件提醒 | 只从 `.env` / 受限文件读取，不落到日志 |
| 只读 + pull | 没有写端点，需要你的代码主动拉 | 服务端定时/手动刷新 + 本地 JSON 缓存 |
| **中国区主机不同** | 中国区账号实测使用 `personal.connectapi.garmin.cn`；全球区是 `.com` | `GARMIN_REGION=cn|global` 自动切换，也可 `GARMIN_API_BASE` 显式覆盖 |
| 剥离专有指标 | 经 API 下发的 .FIT 会移除 VO2 max、恢复时间、训练效果、乳酸阈值等未公开字段 | **不影响本项目**：睡眠、心率、HRV、卡路里都在公开字段内 |
| 字段可能因设备/地区不同 | 官方不保证所有账户返回相同的完整字段 | 多候选字段映射 + 找不到留 `None` + 原始 JSON 全量留存供核对 |

官方资料：[Personal API FAQ](https://developer.garmin.com/gc-developer-program/personal/personal-faq) · [Swagger 端点文档](https://developer.garmin.com/gc-developer-program/personal/swagger-docs/) · [官方 Python 示例](https://github.com/garmin/connect-personal-api-python)

### 2.4 认证流程

```
POST {base}/personalapitoken-service/personal-api/personal-api-key/token
     Authorization: ApiKey <Personal API Key>
     （不要带 Accept: application/json，否则 406）
  →  access_token（24h，JWT，纯文本响应体）

GET  {base}/sleep-service/sleep/dailySleepSummariesByDate?...
     Authorization: Bearer <access_token>
     Accept: application/json（406 时自动重试 Accept: */*）
```

`base`：
- 全球区 `https://personal.connectapi.garmin.com`
- 中国区 `https://personal.connectapi.garmin.cn`

创建 Key 的页面：全球区 [connect.garmin.com/app/settings/personalApiToken](https://connect.garmin.com/app/settings/personalApiToken)，中国区 [connect.garmin.cn/app/settings/personalApiToken](https://connect.garmin.cn/app/settings/personalApiToken)。

### 2.5 DeepSeek 当前接口（2026-11）

| 项 | 值 |
|---|---|
| 兼容格式 | OpenAI / Anthropic 兼容，本项目用 OpenAI 格式 |
| base_url | `https://api.deepseek.com`（兼容 `/v1`，但 `/v1` 与模型版本无关） |
| 端点 | `POST /chat/completions`，`Authorization: Bearer <DEEPSEEK_API_KEY>` |
| 当前模型 | **`deepseek-flash`**（已升级 DeepSeek-V4.1-Flash-1017）、`deepseek-v4-pro` |
| 已弃用 | `deepseek-chat` / `deepseek-reasoner` 于 2026-07-24 弃用，分别映射到 Flash 的非思考/思考模式 |
| 思考模式 | 可选 `thinking: {"type":"enabled"}` + `reasoning_effort`，本项目用 `DEEPSEEK_THINKING=1` 开启 |

> 网上大量旧教程仍写 `deepseek-chat`，直接用会踩弃用告警。本项目默认 `deepseek-flash`。

---

## 3. 架构

```
Garmin Connect Personal API  ──① token 交换 / ② Bearer 只读拉取──┐
                                                                  │
                                    ┌─────────────────────────────▼──────────────┐
                                    │  server.py  (127.0.0.1:8765, 标准库)        │
                                    │  /api/dashboard  /api/config  /api/settings │
                                    │  /api/raw  /api/test  /api/interpret        │
                                    └───┬───────────────┬─────────────────┬──────┘
                                        │               │                 │
                          garmin_client.py  normalizer.py  analyzer.py  llm_interpreter.py
                          (OAuth + 缓存)   (字段映射)      (统计/相关性)   (DeepSeek + 缓存/降级)
                                        │               │                 │
                              data/raw/*.json   data/cache.json   data/interpretation_cache.json
                                        └───────────────┴─────────────────┘
                                                        │
                                     web/index.html + charts.js + app.js（自绘 SVG，零 CDN）
                                                        │
                                              浏览器（本机，浅色主题）

  无凭证 / API 失败 ──► mock_data.py（90 天可复现演示数据）或 web/demo-data.js（离线直开）
```

**技术选型理由**

| 选择 | 理由 |
|---|---|
| Python 标准库 `http.server`，不引 Flask/FastAPI | 只需要 5 个 REST 路由；零安装、零版本冲突，`python server.py` 即可 |
| `urllib` 而非 `openai` / `garminconnect` SDK | 两个上游都是简单 JSON over HTTPS；少一层依赖，凭据流向更可控 |
| 自绘 SVG 图表而非 ECharts CDN | 离线可用、无 CDN 被墙风险、样式完全可控；支持堆叠柱/双轴/参考带/hover/图例开关 |
| 本地 JSON 缓存 + 原始响应留存 | Garmin 拉取有成本、DeepSeek 按 token 计费；同时保留原始 JSON 便于核对真实字段名 |
| 规则化解读降级 | 没配 Key / 模型报错 / 断网时，看板仍给出**基于真实统计值**的解读，不编造数据 |
| 只发聚合统计给大模型 | 不发送逐日健康明细、账户标识或任何凭证；提示词明确禁止编造数字与医疗诊断 |
| 网页配置热更新 | `config.save_settings()` 写入 `.env` 后 `reload()` 重算模块变量；下游每次请求都读 `config.*`，所以不用重启进程。写操作走白名单校验 + 原子替换 + 脱敏响应 |

### 文件结构

| 文件 | 职责 |
|---|---|
| `config.py` | 读取 `.env` / 环境变量 / 受限文件；**网页设置写入 + 校验 + `reload()` 热更新**；脱敏对外状态；地区主机 |
| `garmin_client.py` | OAuth token 交换与缓存、401 重试、范围端点 + 逐日端点降级、原始响应落盘 |
| `normalizer.py` | 原始 JSON → 统一按日记录；多候选字段映射，缺失留 `None` |
| `analyzer.py` | 7 日均线、环比、最小二乘趋势、异常日（±1.6σ）、Pearson 相关、周聚合、规则解读、LLM 输入摘要 |
| `llm_interpreter.py` | DeepSeek 调用、JSON 解析、按 payload 哈希缓存、失败降级 |
| `mock_data.py` | 90 天可复现演示数据（含周周期、趋势、连续下滑、缺失日） |
| `server.py` | HTTP 服务与 REST API |
| `build_demo.py` | 生成 `web/demo-data.js`（7/14/30/90 天四套离线响应） |
| `launcher.py` | **桌面图标入口**：端口复用 / 启动服务 / 等待就绪 / 打开浏览器；`--stop` 停止服务 |
| `create_shortcut.py` | 在桌面创建带自定义图标的快捷方式（需 pywin32，仅构建时） |
| `make_icon.py` | 生成 `web/favicon.ico`（需 Pillow，仅构建时） |
| `start_dashboard.vbs` / `stop_dashboard.vbs` | WSH 备用启动 / 停止入口 |
| `web/` | 看板页面、样式、SVG 图表引擎、主逻辑、**设置面板**、离线数据、favicon |

---

## 4. 用到的 Garmin 端点

官方 Personal API 1.0.0 Swagger 共 26 个 GET 端点，本项目只取所需：

| 用途 | 方法 / 路径 | 参数 |
|---|---|---|
| 连通性验证 | `GET /userprofile-service/userprofile/settings` | — |
| 睡眠（范围，优先） | `GET /sleep-service/sleep/dailySleepSummariesByDate` | `startDate`, `endDate` |
| 睡眠（单日，降级） | `GET /sleep-service/sleep/dailySleep` | `date` |
| HRV（范围，优先） | `GET /hrv-service/hrv/daily/{fromDate}/{toDate}` | 路径参数 |
| HRV（单日，降级） | `GET /hrv-service/hrv/{calendarDate}` | 路径参数 |
| 日汇总（范围，优先，含卡路里） | `GET /usersummary-service/v2/dailysummaries` | `startDate`, `endDate` |
| 日汇总（单日，降级） | `GET /usersummary-service/usersummary/daily` | `calendarDate` |

范围端点若报错（例如参数名或地区差异），客户端会**自动降级为逐日端点**（4 线程并发），并在看板顶部提示。降级不影响正确性，只是请求数变多。

### 统一数据模型（每条日记录）

```jsonc
{
  "date": "2026-11-12",
  "sleep":    { "score", "duration_min", "deep_min", "light_min", "rem_min",
                "awake_min", "spo2", "respiration", "resting_hr", "avg_hrv" },
  "hrv":      { "nightly_avg", "weekly_avg", "night_high",
                "baseline_low", "baseline_high", "status" },
  "calories": { "total", "active", "bmr", "steps", "resting_hr",
                "stress", "body_battery", "intensity_min" }
}
```

所有值都可能为 `null`。**缺失不会被当作 0**，也不参与均值、斜率、相关性计算。

### 字段映射（节选）

> 下面把**实测 2026-11 官方返回**的字段放在最前，旧版/其他设备的候选路径保留在后，逐个回落。

| 统一字段 | 候选原始字段（按优先级） | 换算 |
|---|---|---|
| `sleep.score` | **`sleepScores.overall.value`** → `sleepScore.value` → `dailySleepDTO.sleepScore.value` | — |
| `sleep.duration_min` | `sleepTimeSeconds` → `dailySleepDTO.sleepTimeSeconds` → `sleepDurationSeconds` | 秒 ÷ 60 |
| `sleep.deep_min` | `deepSleepSeconds` → `dailySleepDTO.deepSleepSeconds` | 秒 ÷ 60 |
| `hrv.nightly_avg` | `lastNightAvg` → `hrvSummary.lastNightAvg` → `value` | — |
| `hrv.baseline_low/high` | `baseline.balancedLow / balancedUpper`（含 `hrvSummary.` 前缀） | — |
| `calories.total` | **`calories.burnedTotal`** → `totalKilocalories` → `totalCalories` | — |
| `calories.active / bmr` | **`calories.burnedActive` / `calories.burnedResting`** → `activeKilocalories` / `bmrKilocalories` | — |
| `calories.steps` | **`movement.steps.value`** → `totalSteps` → `steps` | — |
| `calories.resting_hr` | **`heartRate.restingValue`** → `restingHeartRate` | — |
| `calories.stress / body_battery` | **`stress.avgLevel` / `bodyBattery.latestValue`** → 旧版同义字段 | — |
| `calories.intensity_min` | **`intensityMinutes.moderate`** → `moderateIntensityMinutes` | — |

日汇总端点是嵌套结构：`calories.*`、`movement.steps.value`、`heartRate.*`、`stress.*`、`bodyBattery.*`，而不是旧教程里的扁平 `totalKilocalories` / `totalSteps`。

完整候选表在 `normalizer.py` 的 `FIELD_CANDIDATES`。**如果你的账号返回了表里没有的字段名**：
1. 点看板「查看原始响应」，或打开 `data/raw/latest.json`；
2. 找到对应字段路径；
3. 在 `FIELD_CANDIDATES` 里补一条候选即可，不用改其他代码。

> 注意：字段映射只做「已知字段名」的匹配；找不到就留空，绝不猜含义、不把缺失当 0、不据此编造平均值。看板「数据与字段」卡片会显示每个指标的有效天数。

---

## 5. 配置项

**推荐在网页「⚙ 设置」里改**：除 `PORT` 和 `*_FILE` 外，下表所有项都能在面板里修改并即时生效。手工编辑 `.env` 仍然有效；面板保存时会重新加载它。写操作只接受白名单字段，密钥只回传脱敏提示。

| 变量 | 默认 | 网页可改 | 说明 |
|---|---|---|---|
| `GARMIN_PERSONAL_API_KEY` | 空 | ✅ | Garmin Connect 网页版自助生成的 Personal API Key |
| `GARMIN_PERSONAL_API_KEY_FILE` | 空 | — | 可选：从权限受限文件读取 Key（优先级低于环境变量） |
| `GARMIN_REGION` | `global` | ✅ | `global` / `cn`；**中国区账号必须设 `cn`** |
| `GARMIN_API_BASE` | 按 region 推导 | ✅ | 高级：显式覆盖 API 主机；留空即恢复按地区自动 |
| `DEEPSEEK_API_KEY` | 空 | ✅ | DeepSeek 平台 API Key |
| `DEEPSEEK_API_KEY_FILE` | 空 | — | 可选：从受限文件读取 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | ✅ | 兼容 OpenAI 的服务地址，可换自建代理 |
| `DEEPSEEK_MODEL` | `deepseek-flash` | ✅ | 或 `deepseek-v4-pro` |
| `DEEPSEEK_THINKING` | `0` | ✅ | `1` 开启思考模式（更慢更贵，分析更细） |
| `DATA_SOURCE` | `auto` | ✅ | `auto` / `garmin` / `mock` |
| `DEFAULT_DAYS` | `30` | ✅ | 默认时间范围（7 / 14 / 30 / 90） |
| `CACHE_TTL_MINUTES` | `120` | ✅ | Garmin 数据缓存时长 |
| `PORT` | `8765` | ❌ | 本地端口，启动参数，改动需重启 |
| `HTTP_TIMEOUT` | `20` | ✅ | 上游请求超时（秒） |

**保存时的自动动作**：写 `.env`（原子替换、仅所有者可读写）→ `reload()` 重算模块变量 → 若 Garmin 配置变化则丢弃 token / 数据 / 原始响应缓存，若 DeepSeek 配置变化则丢弃解读缓存 → 前端强制刷新数据。全程不需要重启进程。

### 本地服务 API

| 路由 | 方法 | 说明 |
|---|---|---|
| `/`、`/app.js`、`/charts.js`、`/style.css`、`/favicon.ico` | GET | 静态资源 |
| `/api/config` | GET | 脱敏配置状态 + 配置指引 |
| `/api/settings` | GET | 设置面板初值（密钥只给 `configured` / `****abcd`）、可选地区与模型、`.env` 路径与可写状态 |
| `/api/settings` | POST | 白名单校验后写入 `.env` 并热更新；返回变更项与失效缓存标记 |
| `/api/dashboard?days=30&refresh=1` | GET | 完整看板数据（含统计、相关性、AI 解读） |
| `/api/raw` | GET | 最近一次 Garmin 原始响应（字段核对用） |
| `/api/test` | POST | `{"target":"garmin"}`（默认）分 token / profile 两阶段验证，返回 `stage`、`status`、可操作 `hint`；`{"target":"deepseek"}` 轻量验证模型接口 |
| `/api/interpret` | POST | 强制重新生成 DeepSeek 解读（绕过缓存） |

---

## 6. 安全约定

1. 服务只监听 `127.0.0.1`，不对外暴露。
2. Key 只从环境变量 / `.env` / 受限文件读取；**绝不要粘贴到聊天、工单或共享文档**，也不要提交到 Git（`.env` 已列在 `.gitignore` 建议项里）。
3. Key **只**发送给官方 token endpoint；所有业务请求只带 24 小时 Bearer token。
4. 日志、API 响应、前端页面都不含凭证（只显示 `****abcd` 尾四位提示）。
5. token 缓存、数据缓存、解读缓存都尝试设为仅所有者可读写（Windows 上 `chmod` 语义有限，建议把项目放在个人目录）。
6. 发给 DeepSeek 的只有聚合统计（周均值、环比、异常日、相关系数），**不含逐日明细、姓名、账户标识或密钥**。
7. Key 到期或怀疑泄露：去 Garmin Connect 设置页删除旧 Key，重新生成，然后在网页「设置」里替换并保存（立即生效）。若手工编辑 `.env`，需重启服务或在设置面板保存一次触发重新加载。
8. `/api/settings` 只接受白名单字段，密钥绝不出现在任何 API 响应、日志或前端页面；写 `.env` 使用临时文件原子替换。

---

## 7. 常见问题

| 现象 | 处理 |
|---|---|
| 一直显示"演示数据" | 没检测到 `GARMIN_PERSONAL_API_KEY`。点「设置」填入 Key 保存即可，无需重启；也可能是「数据源」被设成了 `mock`，切回 `auto` |
| 中国区账号 token 交换失败 | 在「设置」里把「API 地区」切到 **中国区**（主机换成 `personal.connectapi.garmin.cn`），保存后用「测试 Garmin」看状态码。单个账户成功不代表所有中国区账户都通用，以官方与你账户的实际返回为准 |
| **全部端点报 HTTP 406** | 这是 **token 交换阶段的内容协商失败**，不是参数名问题（看板顶部会明确标「认证阶段失败」）。当前版本已修复：token 请求不带 Accept、其余请求 406 自动用 `*/*` 重试。若仍出现，点「查看原始响应」看 `errors.auth.path`，并确认中间代理没有改写请求头 |
| 顶部提示「认证阶段失败」 | 先看 `errors.auth.status` 与 `hint`；401/403 多为 Key 或地区不匹配，404 多为主机选错，406 见上一行。认证失败时不会发起逐日降级 |
| HTTP 401 | Key 失效/被撤销，或 token 过期。代码会自动重换一次；仍失败就去设置页重建 Key |
| 范围端点报 400 | 看板会自动降级到逐日端点。也可对照 [Swagger](https://developer.garmin.com/gc-developer-program/personal/swagger-docs/) 核对参数名，必要时改 `garmin_client.py` 的 `calls` |
| 某个指标全空 | 说明该账户/设备没返回这个字段。点「查看原始响应」核对，然后在 `FIELD_CANDIDATES` 补候选路径；**不要手工填 0** |
| DeepSeek 解读没生效 | 检查 Key、余额、网络、`DEEPSEEK_MODEL`。失败会自动降级为规则解读并在 AI 卡片标注原因 |
| 想少烧 token | 解读结果按统计内容哈希缓存，同一份数据重复刷新不重复计费；只有点「重新生成解读」才强制重算 |
| 想换成非官方库取数 | 只需替换 `garmin_client.py`，保持 `fetch_range()` 返回结构不变即可；但不推荐——官方 Personal API 已足够且更稳 |
| 只想在别的机器上看静态页 | 直接打开 `web/index.html` 即可（内置 7/14/30/90 天离线演示数据），无需后端；离线模式下「设置」会提示先启动本地服务 |
| 双击桌面图标没反应 | 看板会等 25 秒端口就绪；失败时弹系统提示并写 `data/launcher.log`。常见原因是端口被别的程序占用（改 `.env` 的 `PORT`），或 managed Python 路径变化（重跑 `create_shortcut.py`） |
| 重复双击图标 | 不会重复起服务：检测到端口已有本看板的服务时，直接打开浏览器复用 |
| 怎么停服务 | 关闭那个 `Garmin Health Dashboard - close this window to stop` 控制台窗口；或 `python launcher.py --stop` / 双击 `stop_dashboard.vbs` |
| 设置保存失败 | 面板会显示具体原因（字段校验 / `.env` 不可写）；检查项目目录权限，或看 `/api/settings` 返回的错误信息 |

重新生成离线数据（改了 `mock_data.py` 或分析逻辑后）：`python build_demo.py`

---

## 8. 免责声明

本项目是个人数据的**描述性统计**工具，不构成医疗建议或诊断。API 的可用性、字段完整性与服务条款以 Garmin / DeepSeek 官方最新页面和你账户的实际返回为准。
