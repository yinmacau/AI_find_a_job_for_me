---
name: qiuzhi-pipeline
description: 执行 npe_get_jobs 求职自动化流程（BOSS 岗位发现 → DeepSeek 简历匹配 → 批量打招呼投递），含登录刷新、补导模式、数据写回与各类报错的处理规程
type: prompt
whenToUse: 当用户要求跑求职流程、发现新岗位、投递岗位、刷新 BOSS 登录、补导库中岗位、或排查 npe_get_jobs 项目的报错时使用
arguments:
  - stage
---

#求职自动化流程

项目根目录：``（以下所有命令都在该目录、conda base 环境的 `python` 下执行，**不要用 WindowsApps 的 python3.12.exe**）。

可选参数 `$stage`：`discover`（只发现+匹配）/ `send`（只投递）/ `login`（只刷登录）/ 空或 `all`（全流程）。用户提到具体阶段时按阶段执行，否则跑全流程。

## 步骤 0：前置检查

1. 服务健康检查：`GET http://localhost:8081/actuator/health` 和 `GET http://localhost:17321/health`。
2. 任一不通：Java 后端未启动——后台启动 `cd C:/Users/YHR/npe_get_jobs && "C:/Users/YHR/.jdks/ms-21.0.8/bin/java" -jar target/npe_get_jobs-v1.1.0.jar`。**必须用 ms-21.0.8 的 JDK21**（jar 为 Java 21 编译，PATH 默认 java 17 会报 `UnsupportedClassVersionError`）。
3. 若刚改过 Java 代码：先 `D:/apache-maven-3.9.11/bin/mvn package -DskipTests` 重新打包（mvn 不在 PATH），再重启。Python 脚本改动保存即生效，无需打包。

## 步骤 1：确认 BOSS 登录态

- 跑 `python login_boss.py`：弹出受控 Chrome，已登录则秒过，未登录则等用户扫码（最多 180 秒）。
- 每次全流程开始前都执行一次（BOSS 登录态约 1 小时过期，发现+匹配一轮 30-60 分钟，先刷新可避免中途 409）。

## 步骤 2：岗位发现 + 简历匹配

- 命令：`python job_discovery.py --match-resume`（后台运行，长任务，完成后自动通知）。
- **铁律：同一时刻只允许一个 job_discovery 实例**。启动第二个会报 `HTTP 409 workflow already running`——这不是要修复的错误，先确认没有别的实例在跑。
- **模式轮换（commit-on-success）**：无参数时双模式轮换（预设方向轮询 3 个/批 ↔ 简历驱动），`discovery_rotation_state.json` 的 `last_mode` 只在**本轮全部搜索成功完成**后才提交；中断或风控失败不提交，下次重试同一模式（预设模式已跑完的方向从 remaining 移除，续跑剩余）。显式插队不受轮换影响：`--fuzzy`（17 个关键词全量）、`--directions "a,b"`、`--direction "x"`、`--goal "..."`、`--from-resume`。
- **补导模式 `--export-db`**：把后端库中「已发现但未投递」（status 0/1 且未联系）的岗位作为第 0 批并入归档，与搜索结果按 `platformJobId` 合并去重；崩溃/搜索失败也能捞回库中资产。建议每轮发现都带上。
- **增量归档**：每跑完一个搜索任务立即重写 `ai_filtered_nonoutsourced.json`；Ctrl+C 会先归档再退出（已完成搜索不丢，仅正在轮询中的那个搜索会丢，下轮重搜自动去重）。
- 搜索语义：关键词模式下岗位标题必须**包含关键词字样**（子串匹配）；简历驱动模式忽略关键词过滤，公共条件只保留「深圳 10K以上」，岗位取舍交给 DeepSeek。
- 搜索中途失败（疑似风控）会中止剩余任务并保留进度：重跑本脚本即可。

## 步骤 3：DeepSeek 匹配（`--match-resume` 自动触发，默认增量）

- 产物（跨轮累积，不被发现环节覆盖）：`resume_matched_jobs.json`（≥阈值进投递池）、`resume_greeting_messages.json`（专属招呼语）、`resume_low_score_jobs.json`（低分留档）。
- 默认 `--rescore-missing`：按 `href` 只评新岗位，已评过的不重复花 Token，新旧结果合并写回。单独跑：`python resume_matcher.py --rescore-missing`。
- 注意：`ai_filtered_nonoutsourced.json` 每轮发现会**重建**（只含当轮 + 补导批次）；旧轮岗位从其中消失不影响匹配/招呼语产物，岗位本体仍在后端库可由 `--export-db` 捞回。
- 报错处理：
  - `finish_reason=content_filter` → 安全过滤，重试无用，告知用户检查输入；
  - `finish_reason=length` → 代码自动翻倍 max_tokens（上限 16000）重试，自愈；
  - 空内容 / HTTP 429 → 限流或抖动，等 10-20 分钟重跑；
  - DeepSeek 持续不可用 → 拆两步：先 `python job_discovery.py`，恢复后单独 `python resume_matcher.py`。

## 步骤 4：批量投递

- 命令：`python run_contact_sender.py`（后台运行）。默认读 `resume_matched_jobs.json` + `resume_greeting_messages.json`；缺文件明确报错，**绝不回退到通用招呼语**。
- **投递结果写回 DB**：每个 SUCCEEDED / 已沟通过的岗位按 `encryptJobId→DB id` 调 `PUT /api/jobs/{id}/contacted` 置 `isContacted=true`（失败仅告警不阻断）。写回后 Java 双侧（旧链待投递池 `findPendingJobsAsDTO`、新链工作流 `executePreview`）都会跳过该岗位——**这是防重复打招呼的权威机制**，`contact_results*.json` 同步作为本地审计与去重来源。
- 投递前自动排除（加载阶段完成，不进聊天）：已投递+已沟通、含「实习生,实习」关键词、年限要求 4 年及以上。
- 熔断：连续失败 ≥2 即停止（疑似风控），进度保留，修复后重跑自动续上；`ALREADY_CONTACTED` 不计入失败。
- 辅助参数：`--dry-run` 空跑、`--start/--limit` 切片、`--no-exclude-sent` 强制重投。
- 用户反馈投递异常：先看 `contact_results.json` 对应岗位的 status/reason 定位。

## 关键配置位置（改逻辑前先和用户确认）

- `job_discovery.py` 顶部：`DEFAULT_DIRECTIONS`（17 个关键词）、`GOAL_SUFFIX`（关键词模式，「深圳 10K以上 全职 非外包」）、`RESUME_GOAL_SUFFIX`（简历驱动，「广东 10K以上」）、`INTERVAL_MIN/MAX_SECONDS`、`MAX_SCROLL_PAGES`、`ROTATION_BATCH_SIZE`。
- `resume_matcher_config.json`：DeepSeek key/model、`score_threshold`（默认 60）、简历路径。
- `resume_matcher.py` 顶部：`MATCH_BATCH_SIZE`（默认 3）、`DEFAULT_ASSUMED_SKILLS`。
- `run_contact_sender.py` 顶部：`EXPERIENCE_REQUIRED_PATTERN`、`DELAY_SECONDS`、`FAIL_ABORT_THRESHOLD`。

## 数据架构速记（排查问题时先想这张表）

- 单一事实源方向：DB（JobEntity）。状态机 `0=PENDING / 1=PENDING_DELIVERY / 2=FILTERED / 3=DELIVERED_SUCCESS / 4=DELIVERED_FAILED`；`isContacted` 为投递去重权威（2026-09-10 起双链消费）。
- 双链并存：旧链 `JobDeliveryService`+`JobFilterService`（定时全量），新链 `RecruitmentWorkflowService`+`RecruitmentJobSelectionService`（工作流 API 驱动）。两套筛选规则未统一，已知遗留问题见 `.converge/done/20260910-filter-logic-audit/round-1.md`（F6-F13 未修）。
- 防风控节奏：搜索间隔 45-90s 随机、投递间隔 10-15s+抖动、单搜索最多翻 3 页。调参建议见 `docs/anti-detection-recommendations.md`。
- 每次代码变更必须登记 `docs/dev-log.md`（文件顶部、倒序），规范见 `CLAUDE.md`。

## 汇报约定

每个阶段结束向用户简报：发现多少岗位（含补导多少）、匹配通过/低分各多少、投递成功/失败/跳过各多少、产物路径、本轮模式与下轮默认模式。遇到 409/熔断/DeepSeek 错误时给出下一步指令，不要静默重试超过 2 次。
