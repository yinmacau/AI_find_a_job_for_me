---
round: 1
reviewer_backend: unknown
generated_at: 2026-09-10T05:33:36.461432+00:00
invocation_id: 45ad3cfc-c84e-4ae8-bba8-b5ecfed48059
reservation_id: fe0f51d9bc96
reviewer_instance_id: agent-2
verdict: 阻断需修复
terminal_decision_event_id: df17f400-ede7-41f6-b365-eb11875455ea
terminal_decision_value: 阻断需修复
---
# (skeleton)

## Reviewer 完整输出

(pending)

## Orchestrator 处理记录

(pending)

## 审计 findings 全文（Reviewer agent-2 输出）

verdict: 阻断需修复（2×P0, 4×P1, 6×P2, 3×P3 + 4 suggestion）

### P0
- **F1 新旧链路共享 JobEntity 状态机但互不感知**：新链注册置 PENDING（RecruitmentJobRegistryService:75），旧链 JobDeliveryService:153 → JobService.findPendingJobsAsDTO:473 按 status==PENDING 收集并用旧链规则重评——新链筛掉的岗位可能被旧链投递；更严重的是新链 contact 成功后不写回任何状态，岗位永久 PENDING，被旧定时链再次投递 = 确定性重复打招呼路径。
- **F2 新链无已联系去重**：select() 六条规则无 isContacted；selectedById 仅单次运行内去重；跨 goal/跨批次重复运行会对已沟通 HR 重复发 greeting。Python 端 load_sent_job_ids 只作用于导出文件，不影响 Java contact 路径。

### P1
- **F3** JobFilterService:182 对 `config.getEnableAIJobMatchDetection()` 自动拆箱，ConfigDTO 加载异常路径下该字段为 null → NPE 回滚整个 @Transactional 过滤（新链用 Boolean.TRUE.equals 安全写法，两链不一致）。
- **F4** job_discovery.py:51-54 DEFAULT_DIRECTIONS 缺逗号 → Python 隐式字符串拼接，4 个方向融合成 'AI 应用产品AI 产品AI 初级产品AI 产品助理'（已实测复现），该融合串永远匹配不到岗位，每次轮询白跑完整工作流。
- **F5** 旧链黑名单两处 NPE：jobName 未判 null（:240-241）、黑名单元素未过滤 null（String.contains(null) NPE）。

### P2
- **F6** 新链薪资正则 `(\d{1,3})\s*[-到至~]\s*(\d{1,3})\s*[kK]` 覆盖窄且 fail-open："15千"、"20K以上"、"15元·天" 静默通过，goal 薪资对它们失效；旧链也不支持"千/元·天"但支持日薪转换，两链结论可不同。
- **F7** smartMatch 对 title+description 均空的岗位抛 IllegalArgumentException，穿透 stream 使整个工作流 FAILED（一脏岗位毁一批）；AI 调用无超时/熔断，挂起时 activePlatforms 锁不释放，后续 start() 报 "workflow already running"。
- **F8** 旧链每岗位 3 次 userProfileRepository.findAll() 全表扫描；新链每批次 1 次；executePreview 先注册后筛选，中途失败留 PENDING（与 F1 叠加）。
- **F9** 重复 encryptJobId 实体选择策略不一致：filterJobs 保留第一条（:76），updateJobAIResult 取 createdAt 最新（:329-331），AI 结果与状态可能写到不同记录。
- **F10** 城市 null 语义两链相反：新链 goal.cities 非空时 city=null 被过滤，旧链 cityCode=null 放行；start() 先开浏览器会话后查冲突，冲突时会话泄漏。
- **F13（原编号）** snapshot() filtered 与 matched 同传 selected.size()，统计失真；filterJobs 把实体缺失的岗位计入"被过滤"。

### P3
- **F11** blackRecruiters 全库无写入来源，旧链"招聘者黑名单"是恒空死代码。
- **F12** goal 经验年限解析入库后 Java 筛选层完全不消费（纯死数据，待确认浏览器层是否消费）。
- 旧链 filterJobs 统计口径、新链 select 无招聘者维度等见 reviewer 原报告。

### suggestion（非缺陷）
- job_discovery.py 每次运行新建 goal 实体无清理，长期累积。
- selectedById 以 platformJobId 为 key，null id 岗位互相覆盖。
- resume_matcher.py href 为空岗位每次全量重评。
- 新链六规则与旧链八规则双轨维护，建议收敛单一规则引擎（与本审计前讨论一致）。

## Orchestrator 处置说明

用户约束「现阶段禁止修改代码」→ 不进入 Executor 修复轮。本审计的产物即 findings 清单本身，交付用户决策修复优先级。F1/F2 与用户此前关心的防重复打招呼/封号风险直接相关，建议最优先。
