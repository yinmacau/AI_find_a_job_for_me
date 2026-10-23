# Retrospective — 20260910-boss-dialog-llm-decision

## Terminal decision

- 类型：终止-a（严格通过）——Round 1 fresh reviewer verdict = 阻断需修复（1 项 implementation 级 B1）→ Executor 修复 → Inner Loop 同实例 reviewer 验收 verdict = **Accepted**，零新阻断，全部 issue resolved
- 轮次：1 outer loop（review + repair）+ 1 inner loop（continue 验收）；未达盲审复核阈值（≥2 outer loops），blind_recheck 不适用
- 振荡：无（无 Type O/R/F/S）

## 产物

BOSS 直聘投递弹窗 LLM 决策改动 + AI 打招呼默认开启：

1. `BossRecruitmentServiceImpl.java`：弹窗检测（多容器 `findVisibleDialog`）→ 快照提取 → LLM 决策（JSON，temperature=0）→ 统一谓词匹配执行（`isButtonMatch`，精确 equals 或 candidate.contains，校验/执行同源）→ 失败/静默路径全部收敛到 `closeDialogAndRetry` 回退
2. `ConfigDTO.java` / `AbstractRecruitmentService.java`：`enableAIGreeting` 默认值 null→false 改 null→true（用户裁决：默认全部打招呼经 AI 生成；显式 false 可关闭）

## Issue 处置

| Issue | Severity | 状态 | 说明 |
|---|---|---|---|
| B1 校验/执行匹配不一致 + 静默失败 | implementation | resolved | isButtonMatch 统一谓词；无匹配 log.warn + 抛异常走回退 |
| S1 弹窗容器检测覆盖不足 | suggestion | resolved | findVisibleDialog 覆盖 .dialog-container/.dialog-con/.dialog-title 父容器 |
| S4 短按钮文本误判 | suggestion | resolved | 随谓词统一消除 |
| USER-1 AI 打招呼默认开启 | 用户外部输入 | resolved | 四处默认值改 true |
| S2 提示词注入残余风险 | suggestion | 接受风险 | action 白名单 + buttonText 白名单已限制影响面；如需加固可后续用分隔符包裹弹窗原文 |
| S3 applyDialogAction 4 参数 | suggestion | 暂不处理 | 风格债，与文件内既有风格一致 |
| S5 LLM 调用无显式超时 | suggestion | 延后 | 底层 HTTP 客户端有默认超时；后续可作硬化项 |
| S6 泛型 throws Exception | suggestion | 暂不处理 | 与文件既有风格一致 |

## 过程记录

- 用户外部输入（Round 1 后）提供了两个运行时问题反馈，与 B1/S1 同源，验证了收敛方向
- boundary_check：全程 pass（orchestrator 未直接修改产物，数据层诊断仅读 DB）
- 确定性检查：每轮 mvn compile（JDK 21）exit 0
- 追踪机制成本：本轮 rule_frequency 仅 boundary_guard 一条活跃，追踪开销可忽略；规则总数 <2 时应重新评估必要性

## 验证状态

- 编译全绿（无该模块单测，Playwright Page 不可单测）
- 运行时验证：弹窗决策需真实投递场景观察日志（`检测到弹窗`/`LLM弹窗决策结果`/`已按LLM决策点击弹窗按钮`）
terminal_decision_event_id: f7a71140-5837-4419-a95b-7af225377820
terminal_decision_value: 可执行
