# BOSS 直聘投递与简历匹配工作流（VSCode 版）

把整套「简历 AI 匹配 → 加载岗位 → 生成招呼语 → 批量投递」流程封装成了 Python 脚本，可以直接在 VSCode 里运行。

## 文件说明

| 文件 | 作用 |
|---|---|
| `run_contact_sender.py` | 主脚本：检查服务、加载数据、批量投递 |
| `.vscode/launch.json` | VSCode 调试配置，按 `F5` 即可运行 |
| `.vscode/tasks.json` | VSCode 任务配置，按 `Ctrl+Shift+B` 即可运行 |
| `ai_filtered_nonoutsourced.json` | 岗位发现脚本输出的原始岗位列表（投递前的中间产物） |
| `greeting_messages.json` | ⚠ 旧版占位招呼语（万能模板，已停止生成；投递脚本不再默认读取它，可被删除） |
| `contact_results.json` | 投递结果（每次运行与历史合并写入，**勿删**） |
| `contact_results_v2.json` | 早期手动备份的历史结果（去重时仍会被读取） |
| `resume_low_score_jobs.json` | 低于匹配阈值的岗位留档（含 AI 给出的欠缺理由，按分数升序） |
| `find_project.py` | 自动检测项目位置 |
| `resume_matcher.py` | 读取简历 + AI 匹配岗位技能 |
| `resume_matcher_config.json` | 简历匹配 API 配置（已加入 .gitignore） |
| `resume_matched_jobs.json` | 匹配后的岗位及分数 |
| `resume_greeting_messages.json` | 匹配岗位的 AI 生成招呼语 |
| `job_discovery.py` | 在 VSCode 中直接调用主应用 API 发现新岗位（关键词模糊搜索 + 简历驱动双模式轮换） |
| `login_boss.py` | 刷新 BOSS 登录态：打开受控 Chrome 等扫码，登录成功后退出（登录态自动持久化） |
| `run_all.py` | 流水线主控：按顺序执行 发现→匹配→投递，阶段间暂停提示 |
| `discovery_rotation_state.json` | 方向轮询进度（自动维护，勿删） |

## 重要：岗位列表从哪来？

`run_contact_sender.py` 本身**不搜索新岗位**，它只读取岗位列表和招呼语两个文件。日常使用中你不需要手动更新它们——用下面的一键流水线或发现脚本即可。

### 方式零：一键流水线（最推荐）

`run_all.py` 按顺序执行整个流程：岗位发现 → AI 匹配 → 批量投递。

```bash
# 交互模式：每个阶段跑完暂停，提示「请按顺序执行下一个文件」，回车继续、q 退出
python run_all.py

# 全自动：一口气跑完
python run_all.py --auto
```

保护逻辑：某阶段失败立即中止（已完成的进度保留）；发现阶段没有新岗位则直接结束，不会空跑投递。

### 方式一：在 VSCode 中直接发现新岗位

使用 `job_discovery.py`，它会自动调用主应用 API。有两种搜索方式：

**方式 A：关键词模糊搜索**——查询词来自 `DEFAULT_DIRECTIONS`（14 个关键词清单，见 `job_discovery.py` 顶部），岗位标题**包含对应关键词字样**即命中（子串匹配，不要求全等），精细筛选交给 DeepSeek 简历匹配。用于：预设方向轮询、`--direction`/`--directions`、`--fuzzy`。

**方式 B：简历驱动搜索**——DeepSeek 读取简历生成查询方向，公共条件**只保留「深圳 10K以上」**（薪资下限，高于也可；不限全职/外包），搜回的岗位**不做关键词字样过滤**，直接由 DeepSeek 比对简历与岗位决定取舍（忽略关键词清单）。用于：轮换中的「简历驱动」档和 `--from-resume`。

**默认是双模式轮换**：不带参数运行时，「预设方向轮询」（方式 A，每次 3 个方向）和「简历驱动搜索」（方式 B）**自动交替**——轮换节奏记在 `discovery_rotation_state.json` 的 `last_mode` 字段里，显式参数（`--directions` 等）手动插队不影响轮换。

```bash
# 自动轮换下一批 + 简历匹配（日常就用这一条）
python job_discovery.py --match-resume

# 模糊搜索：14 个关键词一次跑完（不占用轮换进度）
python job_discovery.py --fuzzy --match-resume

# 强制本次用简历驱动（不占用轮换节奏）
python job_discovery.py --from-resume --match-resume

# 手动指定方向 / 自定义完整目标
python job_discovery.py --directions "大模型评测,LLM 评测,AI QA" --match-resume
python job_discovery.py --goal "深圳 大模型评测 15-20K"
```

> 以后所有脚本统一用 `python xxx.py` 运行（conda base 环境，已装好 python-docx 等依赖）。不要用 WindowsApps 的 `python3.12.exe`，那是另一个解释器。

流程：
1. 按当前模式组装搜索任务（预设轮询从状态文件取本批方向，自动拼接公共条件；关键词模式下顺序随机打乱）
2. 逐个启动 BOSS 直聘岗位发现工作流，任务间隔 45-90 秒随机
3. 关键词模式做标题字样过滤（标题必须含该关键词）；简历驱动模式不过滤
4. 合并去重、排除关键词（默认排除实习生/实习）、排除已投递岗位
5. 保存 `ai_filtered_nonoutsourced.json`；加 `--match-resume` 自动调用 `resume_matcher.py`

关键词清单在 `job_discovery.py` 顶部的 `DEFAULT_DIRECTIONS` 常量里；公共条件在 `GOAL_SUFFIX`（关键词模式，默认「深圳 15K以上 全职 非外包」）和 `RESUME_GOAL_SUFFIX`（简历驱动，默认「深圳 10K以上」）——薪资按下限匹配，高于区间的岗位不会被排除。
某轮跑到一半失败（风控/登录失效）会直接中止，重跑本脚本即可：已投递岗位会自动去重，不会重复处理。

### 方式二：通过网页前端

1. 打开前端页面：`http://localhost:6866`
2. 进入「智能求职」或「招聘平台」模块
3. 修改或重新输入你的求职目标
4. 让系统重新执行「发现 → 过滤 → 匹配」流程
5. 导出/保存后，会生成新的 `ai_filtered_nonoutsourced.json`（岗位列表；招呼语需再运行 `python resume_matcher.py` 生成）

> 你也可以手动编辑 `ai_filtered_nonoutsourced.json`，但不建议，因为招呼语需要跟岗位一一对应。

## 前置条件

1. Java 后端已启动（运行 `start.bat`，或手动执行 `java -jar target/npe_get_jobs-v1.1.0.jar`）
2. 已在 BOSS 直聘完成登录（登录态保存在 `.profiles/boss/default`）
3. 已生成 `resume_matched_jobs.json` 和 `resume_greeting_messages.json`（运行 `python resume_matcher.py` 或 `python job_discovery.py --match-resume` 生成；招呼语由 DeepSeek 结合简历+JD 生成，**投递脚本默认只读这两个文件**）

## 运行方式

### 方式一：VSCode 终端

```bash
python run_contact_sender.py
```

### 方式二：VSCode 调试（推荐）

1. 在 VSCode 左侧文件树选中 `run_contact_sender.py`
2. 按 `F5`
3. 在底部「终端」面板查看输出

### 方式三：VSCode 任务

1. 按 `Ctrl+Shift+B`
2. 选择「批量投递 BOSS 岗位」

## 常用参数

```bash
# 只检查数据，不实际发送
python run_contact_sender.py --dry-run

# 从第 3 个岗位开始投递
python run_contact_sender.py --start 3

# 只投递前 5 个岗位
python run_contact_sender.py --limit 5

# 自定义间隔为 15 秒（默认 10 秒，实际间隔会再加 0-5 秒随机抖动）
python run_contact_sender.py --delay 15

# 强制重新投递已发过的岗位（默认会自动跳过）
python run_contact_sender.py --no-exclude-sent

# 修改排除关键词（默认排除含「实习生/实习」的岗位）
python run_contact_sender.py --exclude-keywords "实习生,实习,兼职"
```

## 失败重投与熔断

- 单轮投递失败后自动重投（默认 `--retries 2`，每轮间隔 `--retry-delay 30` 秒，重试前会等服务恢复）
- **熔断保护**：一轮内失败岗位数 ≥ 2 时立即停止整个投递并报错（疑似触发风控），已成功的岗位重跑时自动跳过
- 结果写入 `contact_results.json` 时与历史记录合并（同岗位新结果覆盖旧结果），不会丢失历史投递记录；**请勿删除 `contact_results*.json`**

## 已投递去重

脚本默认会读取所有 `contact_results*.json`，把里面 `status=SUCCEEDED` 的岗位自动跳过，避免重复投递。

- 已沟通过的岗位（详情页显示「继续沟通」）会被识别为 `ALREADY_CONTACTED` 直接跳过，**不会发第二条招呼语，也不计入熔断失败次数**
- 如果想强制重新投递，加 `--no-exclude-sent`

## 简历与岗位技能 AI 匹配

如果你希望根据简历内容进一步筛选岗位，可以使用 `resume_matcher.py`：

```bash
python resume_matcher.py
```

### 流程

1. 读取 `resume_matcher_config.json` 中配置的简历路径
2. 调用 Deepseek API 提取你的技术栈和核心能力
3. 每 **5 个岗位一批**调用 DeepSeek 与简历比对，逐一给出 0-100 分（含匹配/欠缺技能清单与理由）
4. ≥60 分（阈值在 config 的 `score_threshold`）进入投递池，按分数降序；**低于阈值的岗位留档到 `resume_low_score_jobs.json`**（含欠缺理由，便于复盘调阈值）
5. 为每个通过岗位生成**详细招呼语**（120-200 字）：DeepSeek 结合岗位 JD 要求，把匹配上的技术栈自然写入招呼语，不提短板

### 默认技能

打分时，JD 里出现的以下通用产品技能**视为已具备，不扣分、不计入欠缺**：PRD 撰写、Figma 等原型工具、AI 工具使用（ChatGPT/Claude/Coze/Kimi 等）。列表在 `resume_matcher.py` 顶部的 `DEFAULT_ASSUMED_SKILLS` 常量里，可自行增删。

### 常用参数

```bash
# 只匹配前 3 个岗位（测试用）
python resume_matcher.py --limit 3

# 只生成分数，不生成招呼语（省 Token）
python resume_matcher.py --no-greeting
```

### 用匹配结果投递

```bash
python run_contact_sender.py --jobs-file resume_matched_jobs.json --greetings-file resume_greeting_messages.json
```

这样只会投递 AI 认为匹配度达标的岗位。

## 完整工作流示例

```bash
# 一键流水线（推荐）：发现 → 匹配 → 投递，阶段间可检查
python run_all.py

# 或分步手动执行
python job_discovery.py --match-resume   # 1. 发现 + 匹配
python run_contact_sender.py --jobs-file resume_matched_jobs.json --greetings-file resume_greeting_messages.json  # 2. 投递
```

## 注意事项

- 投递间隔默认 10 秒 + 0-5 秒随机抖动；搜索时每页停留 8-15 秒随机、每个方向最多翻 3 页、方向间隔 45-90 秒随机——这些是为了模拟真人行为、降低被 BOSS 风控的概率，请勿随意调快。
- 搜索阶段和投递阶段都会排除已投递成功的岗位（读取 `contact_results*.json`）；其中「已沟通过（ALREADY_CONTACTED）」的岗位也视为已投递，加载岗位阶段就会跳过，不会重复进入聊天。
- 某个方向搜索失败会立即中止剩余方向；投递时连续失败 ≥2 个会熔断停止。遇到这两种情况请检查弹出的 Chrome 窗口（滑块验证/登录失效），处理后重跑即可，已完成的进度不会丢。
- `job_discovery.py` 会操作真实浏览器搜索 BOSS 直聘，请确保已登录，且运行期间不要开关 VPN（梯子）。**如果报错 `platform session is not authenticated`（HTTP 409），说明登录态过期，先运行 `python login_boss.py` 扫码登录，再重跑发现脚本。**
- 建议在工作日上午 10-12 点、下午 2-6 点运行，每天一批（3 个方向）+ 投递 20-30 个新沟通为宜。
- 结果保存在 `contact_results.json`，可以用 VSCode 直接打开查看。
- `resume_matcher_config.json` 包含 API Key，已加入 `.gitignore`，请勿提交到 Git。
