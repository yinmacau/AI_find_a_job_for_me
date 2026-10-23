# AGENTS.md — Agent_mix_Good_Job_Finder 项目记忆

> 本项目 2026-10-22 由 `npe_get_jobs` 复制改造（混合形态 Agent 实验），核心差异：**新增 Agent 策略顾问层**，设计边界见 `docs/agent-advisor.md`。
> 详细操作手册见 `.kimi-code/skills/qiuzhi-pipeline/SKILL.md`（触发词：跑求职流程/发现岗位/投递/排查报错），架构规范见 `CLAUDE.md`。

## 系统分层（排查问题时先定位层）

```
Python 脚本（你手动跑，只编排）          Java 后端（Spring Boot，必须先启动）
  job_discovery.py   ──HTTP──→  :8081  GetJobsApplication（API/工作流/DB）
  resume_matcher.py  ──调用──→  DeepSeek API（简历匹配/招呼语，与 Java 无关）
  run_contact_sender.py ──→ :8081 会话 + :17321 sidecar（Patchright 直驱浏览器）
  agent_advisor.py ──调用──→  DeepSeek API（策略建议，与 Java/浏览器无关）
        │ 产出 agent_strategy.json（白名单+有界校验后落盘）
        ├→ resume_matcher.py 消费 score_threshold（仅 50~80）
        └→ job_discovery.py 消费 direction_weights（仅 0.5~2.0，新周期轮换池加权）
```

- Agent 顾问**不能碰**：搜索/投递间隔、翻页上限、熔断、去重（这些不读 agent_strategy.json，改动前先和用户确认的原则原样适用）。
- 顾问失败/超时只记日志，不影响流水线主结果；策略文件可人工编辑，消费端会重新校验。

- 启动顺序：Java 后端 →（`python login_boss.py` 刷登录）→ `python job_discovery.py --match-resume` → `python run_contact_sender.py`。
- Python 报 `Java 后端未启动（WinError 10061）` = 后端没起，不是脚本 bug。

## 环境坑（本机实测）

- **JDK 必须用 `C:\Users\YHR\.jdks\ms-21.0.8`**：jar 为 Java 21 编译，PATH 默认 java 17 启动报 `UnsupportedClassVersionError`。
- **mvn 不在 PATH**：用 `D:/apache-maven-3.9.11/bin/mvn`。改 Java 后 `mvn package -DskipTests` 再重启；改 Python 保存即生效。
- python 用 conda base 环境，**不要用 WindowsApps 的 python3.12.exe**。
- 后端.jar：`target/npe_get_jobs-v1.1.0.jar`；健康检查 `:8081/actuator/health`，sidecar `:17321/health`。

## 数据状态机（2026-09-10 起，防重复打招呼的权威逻辑）

- `JobEntity.status`：0=PENDING、1=PENDING_DELIVERY、2=FILTERED、3=DELIVERED_SUCCESS、4=DELIVERED_FAILED。
- **`isContacted` 是投递去重权威**：Python 投递成功/已沟通后写回 `PUT /api/jobs/{id}/contacted`；Java 旧链 `JobService.findPendingJobsAsDTO:473` 与新链 `RecruitmentWorkflowService.executePreview` 均排除已联系岗位。
- 本地 `contact_results*.json` 为审计日志 + 次级去重源（口径含 SUCCEEDED + SKIPPED 且 reason 含 ALREADY_CONTACTED/已沟通）。
- 匹配产物 `resume_matched_jobs.json` / `resume_greeting_messages.json` **跨轮累积**；`ai_filtered_nonoutsourced.json` 每轮发现重建（增量归档：每任务完成即写，Ctrl+C 也会先归档）。`--export-db` 把库中未投递岗位（status 0/1）并入归档。

## 双链并存（已知技术债）

- 旧链：`JobDeliveryService`+`JobFilterService`（定时任务全量）；新链：`recruitment` 模块工作流（API 驱动）。**两套筛选规则未统一**。
- 审计发现 F1-F5 已修（2026-09-10）；**F6-F13 未修**（薪资正则、AI 超时熔断、全表扫描、统计失真等），清单：`.converge/done/20260910-filter-logic-audit/round-1.md`。改筛选逻辑前先读它。

## 行为语义（容易误判的点）

- 模式轮换为 **commit-on-success**：`discovery_rotation_state.json` 的 `last_mode` 仅在本轮全部搜索成功后提交；中断/风控失败 → 下次重试同一模式（预设模式续跑剩余方向）。
- 搜索中途失败中止是**设计行为**（防风控），重跑自动续，不是 bug。
- 投递熔断（连失败 ≥2）同理；`ALREADY_CONTACTED` 不计失败、不重投。
- `SECURITY_VERIFICATION_PAUSE`（sidecar 命中风控信号 + Python LLM 裁决后跳过）同样不计失败、不进已投递去重，下轮自动续投；出现该标记 = 该去浏览器人工完成验证了。
- Java 旧链弹窗 LLM 新增 `PAUSE_TASK` 动作（安全验证/登录过期类），抛出 `SecurityVerificationException` 暂停任务、当前岗位保持待投递；看到"投递任务因安全验证/登录过期弹窗暂停"日志是设计行为，不是 bug。
- 防风控节奏参数（搜索间隔 45-90s、翻页 ≤3、投递 10-15s+抖动）在 `job_discovery.py`/`run_contact_sender.py` 顶部，改动前先和用户确认；调参参考 `docs/anti-detection-recommendations.md`。

## 项目约定

- **任何代码变更必须登记 `docs/dev-log.md`**（文件顶部插入、时间倒序），这是 CLAUDE.md 的硬性规范。
- `job_discovery.py` 无单测，验证方式：`python -m py_compile <file>` + `python -c "import <module>"` + `--help`。
- 新窗口跑流程时，先读 `.kimi-code/skills/qiuzhi-pipeline/SKILL.md` 再动手。
