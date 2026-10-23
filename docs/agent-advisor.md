# Agent 策略顾问（混合形态 Agent 层）设计说明

> 本项目由 `npe_get_jobs` 复制改造而来（复制于 2026-10-22），核心改造就是新增本层。
> 定位一句话：**LLM 从"流水线的工具"升级为"流水线的顾问"，但手脚（执行与护栏）仍在代码里。**

## 为什么是这个形态

原项目是固定流水线 + LLM 做两个子任务（匹配打分、写招呼语）。全自主 Agent 在此场景代价过大：
操作真实招聘账号（风控敏感）、出错有真实社交代价、行为需可审计。混合形态让 LLM 只碰
"开放式判断"（策略调整、异常归因），不碰"必须确定"的事（节奏、去重、熔断）。

## 架构与数据流

```
contact_results*.json ─┐
resume_matched_jobs.json ─┤
resume_low_score_jobs.json ─┼→ agent_advisor.py → DeepSeek（严格 JSON）
discovery_rotation_state.json ─┤        │
logs/schedule_pipeline.log ────┘        ▼
                              白名单校验 + 区间收敛（validate_advice）
                                        ▼
                              agent_strategy.json ──┬→ resume_matcher.py：score_threshold（50~80）
                              （可人工审阅/手改）    └→ job_discovery.py：direction_weights（0.5~2.0）
```

调度：`scheduled_pipeline.py` 早/晚流水线末尾各跑一次顾问（失败只记日志，不影响主结果）；
也可手动 `python agent_advisor.py` 或双击 `scripts/run_advisor.bat`。`--dry-run` 只打印不写文件。

## 能调整什么（有界生效）

| 项 | 区间 | 消费点 | 语义 |
|---|---|---|---|
| `score_threshold` | 50~80 整数 | `resume_matcher.apply_agent_threshold` | 匹配打分的投递阈值 |
| `direction_weights` | 0.5~2.0 | `job_discovery.load_agent_direction_weights` | >=1.3 该方向本周期轮换池出现两次；<=0.7 本周期剔除 |

校验是双重的：`agent_advisor.py` 写入前收敛一次，两个消费端读取时再各校验一次
（越界、未知方向、非数值一律忽略）。`agent_strategy.json` 可人工直接编辑，消费端同样会校验。

## 永远不能调整什么（硬护栏，agent 无接口触及）

- 搜索间隔 45-90s、翻页 ≤3、投递间隔 10-15s+抖动（`job_discovery.py` / `run_contact_sender.py` 顶部常量）
- 熔断阈值（连续失败 ≥2 停止）
- 投递去重（`isContacted` 权威 + `contact_results*.json` 口径）
- 登录、重试、任何与平台交互的节奏参数

以上参数的生效不经过 `agent_advisor.py`，也不读取 `agent_strategy.json`。

## 纯建议输出（不驱动代码，供人工阅读）

- `summary`：一句话总体判断
- `exception_analysis`：异常归因（类别/依据/给人工的处理建议）
- `notes_for_human`：值得人工关注的事项
- `rejected`：本次被校验层拒绝/收敛的建议及原因（留痕，便于判断模型是否在"乱建议"）

## 故障行为

- DeepSeek 不可用 / 输出非 JSON：顾问进程退出码非零，流水线记日志继续，策略文件保持上次内容
- 策略文件损坏：消费端按默认值/空权重处理，并在日志中说明
- 建议全被拒绝：照常写文件（`rejected` 留痕），生效项为空 = 维持现状

## 手动验证

```bash
python -m py_compile agent_advisor.py job_discovery.py resume_matcher.py
python agent_advisor.py --dry-run   # 看一轮真实建议，不写文件
```
