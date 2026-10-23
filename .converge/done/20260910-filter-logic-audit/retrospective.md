# Retrospective — 20260910-filter-logic-audit

## Terminal decision

- terminal_decision_event_id: df17f400-ede7-41f6-b365-eb11875455ea
- terminal_decision_value: 阻断需修复
- 类型：审计收敛（单轮）——Round 1 fresh reviewer（agent-2）完成三层筛选逻辑全量审计，verdict = 阻断需修复（2×P0 + 4×P1 + 6×P2 + 3×P3 + 4 suggestion）
- 用户约束「现阶段禁止修改代码」→ 不进入修复轮；产物 = 审计 findings 清单（round-1.md），已交付用户决策修复优先级
- 轮次：1 outer loop，无 inner loop，无盲审（单轮），无振荡
- boundary_check：pass（未修改任何项目代码）

## 核心结论（按严重度）

1. **F1/F2（P0）共享状态机失控**：新旧链路共用 JobEntity 状态命名空间但互不感知——新链 contact 成功不写回状态，岗位永久 PENDING，会被旧定时链再次投递；新链本身也无已联系去重。确定性重复打招呼路径，且与防封号目标直接冲突。
2. **F3/F5（P1）旧链 NPE 组合**：Boolean 拆箱 + 黑名单 null 防御缺失，一次脏数据回滚整个事务。
3. **F4（P1）Python 侧实测 bug**：DEFAULT_DIRECTIONS 缺逗号隐式拼接，4 个方向融合成永不命中的串，白跑工作流。
4. P2 集中在薪资解析覆盖、AI 异常穿透、N+1 查询、实体选择策略不一致、城市 null 语义相反。
5. P3 为死代码（blackRecruiters、经验年限条件）与统计失真。

## 验证状态

- 确定性检查：mvn compile exit 0；F4 经独立 Python import 实测复现
- 运行时验证：未做（审计性质，只审不改）

## 后续建议（未执行，待用户决策）

- 修复优先级建议：F1+F2 → F4 → F3/F5 → P2 批量 → P3 清理
- F1/F2 的修复应与新链/旧链协调设计一并考虑（状态机归一或链路管辖权划分）
