# AI Find a Job for Me — 个人智能求职流水线

**每天自动找岗位、比对简历、写好招呼语并批量投递——你要做的只有扫码登录和看结果。**

本项目从 [npe_get_jobs](https://github.com/exception-coder/npe_get_jobs) 复制改造（原项目为 MIT 协议），核心变化：

- 新增 **Agent 策略顾问层**（`agent_advisor.py`）——LLM 不直接动手，而是每天分析投递结果、给出策略建议（匹配阈值/方向权重），经白名单与有界校验后生效；执行层（节奏、熔断、去重）仍是确定性代码，Agent 无权绕过。详见 `docs/agent-advisor.md`。

这是一个运行在自己电脑上的自动化求职助手，主攻 xx直聘。它把「搜岗位 → 读 JD → 比对简历 → 写招呼语 → 逐个打招呼」这条每天要花几小时的重复流程，压缩成一句启动命令和两个系统定时任务。

## 与原版 npe_get_jobs 的区别

| 维度 | npe_get_jobs（原版） | 本项目 |
|---|---|---|
| 定位 | 交互式求职工作台（人在 UI 里操作、逐岗确认后发送） | 无人值守自动化流水线（定时任务驱动、批量投递） |
| 核心新增 | — | **Agent 策略顾问层**：LLM 分析每日结果、闭环调整匹配阈值与搜索方向权重 |
| 投递方式 | 保守：默认不联系招聘方，需人工确认才发送 | 自动：按匹配分批量打招呼，已沟通岗位永久去重，触发风控自动熔断 |
| LLM 角色 | 意向解析、JD 匹配（工具） | 匹配打分、招呼语生成（工具）+ 策略顾问、风控裁决（决策，均有护栏） |
| 调度 | 无定时，随用随开 | Windows 任务计划：早 9:00 投递（不足目标自动补跑）、晚 20:00 备池 |
| 简历匹配 | 画像匹配给结论 | 0-100 打分 + 低分留档原因 + 个性化招呼语逐岗生成 |
| 平台范围 | BOSS 已验证，猎聘/51job/智联接线中 | 主攻 BOSS 直聘（Python 链） |

一句话：**原版是"帮你整理岗位的驾驶舱"，本项目是"替你干活的自动投递员 + 会复盘调整的顾问"。**

## 它能做什么

| 能力 | 说明 |
|---|---|
| 智能岗位发现 | 按预设方向（AI 产品评测、大模型评测、AI 产品经理等 14 个方向）轮询搜索，也支持让 AI 读简历自动生成搜索方向；标题模糊匹配 + 每页停留滑动，降低风控概率 |
| 简历匹配打分 | 调用 DeepSeek 逐岗比对简历与 JD，输出 0-100 匹配分和投递建议，低于阈值自动跳过并留档低分原因 |
| 个性化招呼语 | 结合简历技术栈与岗位任职要求，为每个岗位单独生成打招呼内容，不是模板群发 |
| 批量自动投递 | 自动排除实习生、超年限岗位；已投递/已沟通过的岗位在去重库中永久跳过，绝不重复打扰 |
| 定时流水线 | Windows 任务计划程序驱动：早 9:00 投递（不满目标自动补跑「发现+匹配+投递」），晚 20:00 发现+匹配为次日备池 |
| 风控与熔断保护 | 投递间隔随机抖动、方向间随机冷却；连续失败 2 个立即熔断停止；命中平台安全验证自动暂停交还人工 |
| Agent 策略顾问 | 每天分析成功率/失败原因/分数分布，有界调整匹配阈值（50~80）与方向权重（0.5~2.0），拒绝项留痕可审计 |
| 全程留档 | 投递记录、低分岗位、每轮统计全部落盘，随时可追溯 |

## 一天是怎么运转的

```
20:00  岗位发现（方向轮换）→ 简历匹配打分 → 生成招呼语 → 更新投递池
09:00  刷新 BOSS 登录 → 按匹配分从高到低批量投递
       └── 当日不足目标数？→ 冷却 5-10 分钟 → 发现新岗位 → 匹配 → 继续投
```

所有过程写入 `logs/schedule_pipeline.log`，每天回来一句话拿到汇总：投了几个、成功多少、失败原因是什么。

## 工作原理（三层）

```
你（只需：扫码登录、看结果、必要时处理验证码）
        │
  ┌─────┴─────────────────────────────────────────────┐
  │ Python 编排层（job_discovery / resume_matcher /      │
  │ run_contact_sender / agent_advisor）                │
  │   - 读配置、拼证据、调 DeepSeek                      │
  │   - 固定防风控节奏：搜索间隔 45-90s、翻页 ≤3、         │
  │     投递 10-15s+抖动、连失败 ≥2 熔断                  │
  └──────┬───────────────────────┬────────────────────┘
         │ HTTP                   │ HTTP
  ┌──────┴──────────┐      ┌──────┴───────────────────┐
  │ Java 后端         │      │ Node sidecar (:17321)     │
  │ Spring Boot:8081  │      │ Patchright 直驱本机 Chrome │
  │ API / 工作流 /    │      │ 持久会话 = 你的登录态      │
  │ SQLite 岗位库     │      └──────────────────────────┘
  └─────────────────┘
```

关键设计：**防风控节奏、去重、熔断全部由代码强制，不读策略文件、Agent 无权修改**——这是硬护栏，不是提示词约束。

## 环境准备

| 依赖 | 版本要求 | 用途 | 下载 |
|---|---|---|---|
| JDK | **21**（必须，低版本起不来） | 运行 Java 后端 | [Oracle JDK](https://www.oracle.com/java/technologies/downloads/#java21) 或 [Microsoft OpenJDK](https://learn.microsoft.com/java/openjdk/download) |
| Maven | 3.9+ | 构建打包 | [Maven](https://maven.apache.org/download.cgi)（解压后把 `bin` 加入 PATH） |
| Node.js | 20+ | 运行浏览器 sidecar | [Node.js](https://nodejs.org/)（LTS 即可） |
| Google Chrome | 本机已安装 | 浏览器自动化载体 | sidecar 以 `channel: chrome` 启动你日常用的 Chrome |
| Python | 3.11+ | 流水线编排脚本 | [Miniconda](https://docs.conda.io/en/latest/miniconda.html) 或 python.org 官方版 |
| DeepSeek API Key | 充值账户 | 匹配打分 / 招呼语 / 策略顾问 | [DeepSeek 开放平台](https://platform.deepseek.com/) |

验证安装（每个都应输出版本号）：

```bat
java -version      :: 应显示 21.x
mvn -version       :: 应显示 Apache Maven 3.9.x
node -v            :: 应显示 v20.x 或更高
python --version   :: 应显示 3.11+
```

> **常见坑**
> - `java -version` 显示 17 或更低 → 后端启动报 `UnsupportedClassVersionError`。把 JDK 21 的 `bin` 目录放到 PATH 最前面，或在启动脚本中写死 JDK 21 的 `java.exe` 绝对路径。
> - `mvn` 不是内部或外部命令 → Maven 没配 PATH，用 `D:\你的maven目录\bin\mvn` 全路径代替。

## 安装步骤（按顺序执行）

以下命令均在仓库根目录执行（Windows 用 CMD/PowerShell 均可）。

### 1. 克隆代码

```bat
git clone https://github.com/yinmacau/AI_find_a_job_for_me.git
cd AI_find_a_job_for_me
```

### 2. 配置密钥（两个文件，各司其职）

**① 复制 `.env`**（供 Java 后端侧 AI 功能使用，如弹窗 LLM 裁决）：

```bat
copy .env.example .env
```

用记事本打开 `.env`，填写你实际使用的服务（最少填 DeepSeek 三行）：

```properties
DEEPSEEK_API_KEY=sk-你的key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

**② 复制 `resume_matcher_config.json`**（供 Python 流水线使用：匹配、招呼语、策略顾问）：

```bat
copy resume_matcher_config.example.json resume_matcher_config.json
```

填写其中两个字段：

```json
{
  "api_key": "sk-你的key（与上面同一个）",
  "model": "deepseek-chat",
  "resume_path": "C:/你的简历.docx 的绝对路径"
}
```

> 简历必须是 **docx** 格式（脚本用 python-docx 读取）。`resume_matcher_config.json` 已在 `.gitignore` 中，不会被提交。

### 3. 安装 Python 依赖

```bat
pip install python-docx
```

（其余均为标准库，无需安装。）

### 4. 安装 sidecar 依赖

```bat
npm ci --prefix node-services/recruitment-browser
```

### 5. 构建打包（会顺带构建前端并打入 JAR，首次较慢）

```bat
mvn clean package
```

成功后产物为 `target/npe_get_jobs-v1.1.0.jar`。

> 若 Maven 构建前端时报 pnpm 相关错误，请先安装 pnpm：`npm install -g pnpm`。

## 首次运行（手把手）

### 1. 启动后端 + sidecar

```bat
scripts\start.bat
```

正常会看到 Spring Boot 启动日志。**这个窗口要保持开着**（前台运行），关闭即停止服务。

另开一个窗口验证健康状态：

```bat
curl http://localhost:8081/actuator/health
curl http://localhost:17321/health
```

两个都应返回含 `"UP"` / `"ok"` 的响应。若连接被拒绝（`WinError 10061`），说明后端没起，回到上一步看报错。

### 2. 扫码登录 BOSS（只需一次，登录态长期有效）

```bat
python login_boss.py
```

会打开 Chrome 窗口并展示 BOSS 直聘登录页，用手机 BOSS App 扫码。登录成功后状态写入 `node-services/recruitment-browser/.profiles/boss/default`，以后每天自动复用，直到过期。

> 以后报 `platform session is not authenticated` = 登录态过期，重跑本命令扫码即可，已投递岗位不会重复打扰。

### 3. 手动跑一轮完整流程（验证一切正常）

```bat
python job_discovery.py       :: 发现岗位（约几分钟，搜索间隔受风控保护）
python resume_matcher.py      :: 简历匹配打分 + 生成招呼语（按岗位数消耗 token）
python run_contact_sender.py  :: 批量投递（真实发送！不想投就先别跑这条）
```

跑完后看结果文件：

| 文件 | 内容 |
|---|---|
| `ai_filtered_nonoutsourced.json` | 本轮发现的岗位清单 |
| `resume_matched_jobs.json` | 匹配分 ≥ 阈值的投递池（含分数与理由） |
| `resume_low_score_jobs.json` | 被刷掉的低分岗位及原因 |
| `resume_greeting_messages.json` | 每个投递岗位的专属招呼语 |
| `contact_results.json` | 投递结果：成功/失败/跳过及原因 |
| `logs/schedule_pipeline.log` | 全流程日志 |

确认没问题后，把 `run_contact_sender.py` 交给定时任务自动跑。

### 4. 设置定时任务（推荐）

打开 Windows「任务计划程序」，创建两个任务（或导入后修改路径）：

| 任务 | 触发器 | 操作 |
|---|---|---|
| 早投递 | 每天 09:00 | 运行 `scripts\run_morning.bat` |
| 晚备池 | 每天 20:00 | 运行 `scripts\run_evening.bat` |

**注意：两个 .bat 内有本机绝对路径（Python 位置、项目目录），用记事本打开改成你自己的**，否则双击无反应。任务计划程序的「操作」一栏填 .bat 的完整路径，「起始于」填项目根目录。

定时参数可调（编辑 .bat）：

- `--target 80`：当日成功投递目标，不达标自动补跑（默认 80）
- `--max-extra-rounds 4`：首轮之外最多补跑几轮（默认 4）

## 日常使用

- **每天早上看一眼** `contact_results.json` 或日志尾部：投了几个、失败原因、有没有触发风控暂停
- **登录态过期/弹出安全验证**：浏览器窗口会停在验证页，人工完成验证或重跑 `python login_boss.py`，下轮自动续投
- **想看看 Agent 顾问建议**：`python agent_advisor.py --dry-run`（只打印不写文件）；确认无误后去掉 `--dry-run` 让建议生效（早晚流水线末尾也会自动跑，失败不影响投递）

## Agent 策略顾问（混合形态）

- **每天分析**：投递成功率、失败原因、匹配池分数分布、近期异常日志 → DeepSeek 输出严格 JSON 建议
- **有界生效**：匹配阈值仅在 50~80、方向权重仅在 0.5~2.0 生效（写入前收敛 + 消费端再校验）；高权重方向在轮换池中出现两次，低权重方向本周期剔除
- **硬护栏不动**：搜索/投递间隔、翻页上限、熔断、去重全部由代码强制，Agent 无接口触及
- **纯建议留痕**：异常归因与人工注意事项写入 `agent_strategy.json`（可人工编辑，消费端会重新校验），被拒绝的建议也留痕，便于判断模型是否在"乱建议"

## 定制你的搜索方向

预设方向清单在 `job_discovery.py` 顶部的 `DEFAULT_DIRECTIONS`（默认 14 个 AI 相关方向）。把它改成你自己的目标岗位关键词即可；也可在配置中保留 `generate_directions_from_resume` 流程让 AI 读简历生成方向。

排除关键词（公司黑名单、岗位黑名单）同样在该文件顶部常量中。

## 常见问题 FAQ

**Q: 启动报 `UnsupportedClassVersionError`？**
JDK 版本低于 21。换 JDK 21 后重新 `mvn clean package`。

**Q: Python 报 `Java 后端未启动（WinError 10061）`？**
后端没起。先跑 `scripts\start.bat` 并保持窗口打开。

**Q: 报 `platform session is not authenticated`？**
BOSS 登录态过期。运行 `python login_boss.py` 重新扫码。

**Q: 投递中途停了，日志有 `SECURITY_VERIFICATION_PAUSE`？**
命中平台风控（安全验证/滑块）。这是设计行为：去 Chrome 窗口人工完成验证，下一轮自动续投；已暂停的岗位不算失败、不会重投骚扰。

**Q: 报 `ALREADY_CONTACTED`？**
该岗位之前已成功打过招呼，去重保护生效，不是错误。

**Q: DeepSeek 报 429 / 超时？**
API 余额不足或网络问题。流水线会重试；频繁 429 请检查余额或降低定时任务频率。

**Q: 当天投得很少就停了？**
两种正常情况：① 岗位池枯竭（连续两轮无新岗位可投）；② 熔断（连续 2 个投递失败）。看日志尾部确定是哪种。

**Q: 想暂停整个自动化？**
任务计划程序里禁用两个任务，并关闭 `start.bat` 窗口即可。恢复时逆序操作。

## 数据与隐私

- 所有数据都在你自己电脑上：SQLite 库在 `C:\Users\你的用户名\getjobs\npe_get_jobs.db`，浏览器登录态在 `node-services/recruitment-browser/.profiles/`
- `.env`、`resume_matcher_config.json`、`logs/`、所有求职数据 JSON 均已加入 `.gitignore`，不会被提交
- **`.profiles/` 目录绝不要打包上传**——它等于你的 BOSS 账号；API Key 也绝不要截图外发
- 简历文本仅发送给你自己配置的模型服务

## 目录结构

```
├── agent_advisor.py            # Agent 策略顾问（混合形态核心）
├── job_discovery.py            # 岗位发现编排（方向轮换、防风控节奏）
├── resume_matcher.py           # 简历匹配打分 + 招呼语生成
├── run_contact_sender.py       # 批量投递 + 风控 LLM 裁决
├── login_boss.py               # BOSS 扫码登录刷新
├── scripts/
│   ├── scheduled_pipeline.py   # 早/晚定时流水线驱动
│   ├── start.bat / start.sh    # 启动后端 + sidecar
│   └── run_morning.bat / run_evening.bat / run_advisor.bat
├── src/main/java/getjobs/      # Spring Boot 后端（API/工作流/持久化）
├── frontend/                   # Vue 3 工作台（mvn package 时自动构建打入 JAR）
├── node-services/recruitment-browser/  # Patchright sidecar（直驱 Chrome）
├── docs/agent-advisor.md       # Agent 层设计说明
└── target/npe_get_jobs-v1.1.0.jar      # 构建产物
```

## 技术栈

- **后端**：Spring Boot（Java 21）+ SQLite（自动初始化）+ Patchright sidecar（Node.js 20+，驱动本机 Chrome 持久会话）
- **流水线**：Python 3.11+（仅依赖 python-docx，其余标准库）+ DeepSeek API
- **调度**：Windows 任务计划程序（`scripts/scheduled_pipeline.py` 统一驱动）

## 安全与边界

- 只在自己电脑、自己的招聘账号上运行；登录态与浏览器 Profile 均保存在本地
- 已沟通岗位自动跳过，不重复骚扰招聘方；触发平台风控时熔断/暂停，由人工接管
- 本项目仅供学习与技术交流，使用者需自行承担账号安全与遵守招聘平台规则的责任，请合理控制投递频率

## 许可证

基于 [npe_get_jobs](https://github.com/exception-coder/npe_get_jobs)（MIT，Copyright (c) 2024 GET JOBS）二次开发，保留原署名与许可证声明，见 [LICENSE](LICENSE)。
