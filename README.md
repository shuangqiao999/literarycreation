# LiteraryCreation — 文学创作引擎

**以一段小说开头或一份结构化提纲为输入，自动构建人物关系图谱与角色人格，逐轮推演角色交互，最终渲染为完整的文学正文。本地优先，Tauri 桌面应用形态交付。**

---

## 两大创作模式

### ✍️ 自由续写（Freeform）
输入一段小说开头/种子文本。系统自动提取人物、构建关系网、生成角色人格，角色自主选择行动方向，推演出逻辑自洽的后续情节并渲染为正文。

### 📋 蓝图执行（Blueline）
输入结构化提纲（角色设定 + 弧光 + 关键事件），系统按蓝图推演：

- **角色初值**：每个角色从提纲指定的初始状态出发。
- **事件门控**：关键事件在指定轮次被强制推动（hard/soft/optional 三级）。
- **弧光校准**：角色偏离弧光目标时，系统注入"内心两难"叙事引导（非强制回轨），让角色在目标与本能之间自然选择。
- **结局对齐**：报告输出各角色弧光达成度。

两种模式共用五阶段流水线，仅输入与约束方式不同。

---

## 核心架构

### 五阶段流水线

```
种子文本 → Phase 1 本体生成 → Phase 1.6 故事蓝图 → Phase 2 知识图谱 → Phase 3 角色人格 → Phase 4 回合模拟 → Phase 5 散文渲染
```

| 阶段 | 内容 |
|------|------|
| Phase 1 | LLM 定义实体/关系类型 |
| Phase 1.5 | 加载文学风格规则包（5 种内置风格） |
| Phase 1.6 | LLM 生成结构化故事蓝图（大纲/弧光/揭示节奏/麦高芬/知识缺口/主题/支线），含编辑审查 |
| Phase 2 | 语义分块 + LanceDB 向量索引 + Kuzu 知识图谱构建 |
| Phase 3 | 从图谱提取实体 → LLM 生成角色人格（含说话风格 `speech_style`） |
| Phase 4 | 多角色并行模拟：决策 → 规则引擎计分 → 叙事记忆 → 人格反思 → 社会温度 |
| Phase 5 | 散文渲染：逐章生成 → 正典校验 → 场景去重 → 修订流水线 → 合本导出 |

---

## 文学技艺模块（Phase 5 深度能力）

| 模块 | 功能 |
|------|------|
| **叙述者声音代理** | 从种子文本一次性分析叙述者距离/节奏/手法/禁忌词，注入每章 |
| **两阶段渲染** | 骨架生成（事件→因果叙事流）→ 肉身扩写（文学润色+对话丰富） |
| **正典台账** | 死亡角色不可复活、麦高芬不可增殖、揭示层级防泄底 |
| **高潮驱动器** | 中后期注入冲突升级引导 + 情感投资回报校验 |
| **读者体验模拟** | 每章 LLM 模拟读者反馈（困惑度/无聊度/疲劳度/期待），反馈注入下一章 |
| **修订流水线** | 全章生成后对被标记章节做编辑润色（抽象情感→感官细节、平淡对话→潜台词） |
| **展示不告知** | Prompt 级强制：禁用"她感到愤怒"，改为感官动作传导情绪 |
| **时间感** | 禁用"第二天一早"，改为物理细节标记时间流逝 |
| **章尾钩子检测** | 检测章尾 300 字是否有悬念/反转/意象，缺失则提示 |
| **节奏分析** | 动作密度 vs 反思密度，连续同向偏离时注入节奏建议 |
| **故事重量平衡** | 跨章检测情节密度偏移，防止某章全是转折某章全是过渡 |
| **主题追踪** | 蓝图定义主题 → 跨章检测出现频率 → 连续 3 章未触及提醒回响 |
| **支线生命周期** | 支线 beats 调度 + 活跃期维持 + 结期收束提醒 |
| **母题培养** | 区分蓄意重复（意象回响）vs 意外重复（口头禅），章首尾短语自动观察升级 |
| **意象跨章追踪** | 10 组概念词网（水/火/牢笼/面具/路/伤口/光暗/归属/风/沉默）全局扫描连续性 |
| **结局专属约束** | 最后一章：不可逆决定、余韵收尾、一个未回答的问题 |
| **多 POV 交叉剪辑** | 多视角时每个视角独立渲染后交织 |
| **对话风格事后检测** | 每章生成后检测角色对话是否符合 `speech_style` 约束 |

---

## 模拟引擎能力（Phase 4）

| 模块 | 功能 |
|------|------|
| **复合情感合成** | 6 指标交叉映射 → 矛盾心理描述（如 trust 低 + affection 高 = "你心里装着一个人，但每次靠近都像踩薄冰"） |
| **情感投资追踪** | 每轮记录角色感情投入额，高潮章校验回报比例 |
| **社会温度系统** | 5 维自动累积：舆论/流言/派系张力/亲密压力/外部威胁 |
| **人格动态反思** | 事件驱动批量触发（经历累积/指标剧变/关系质变/空闲保护），LLM 产出行为准则 + 语风演化 |
| **私人记忆分类** | 情感关系独立队列（承诺/背叛/恩情/爱慕/仇恨），不受 FIFO 容量淘汰 |
| **叙事记忆** | 每轮场景片段存入角色记忆，注入后续决策 |

---

## 5 种内置文学风格

| 风格 | 特点 |
|------|------|
| `literary_realism` 现实主义 | 平衡指标，重日常细节 |
| `literary_romance` 浪漫主义 | 高初始 affection，强情感效应 |
| `literary_suspense` 悬疑 | 高初始 tension/mystery（70），调查研究 +18 mystery |
| `literary_epic` 史诗 | 高初始 power，大冲突效应 |
| `literary_court` 宫廷剧 | 权力博弈权重最高，强化 manipulate/betray 效果 |

每个风格定义 6 个指标（trust/tension/affection/power/mystery/fatigue，0-100）+ 8 种动作（confront/confess/ally/betray/investigate/protect/manipulate/observe）+ 自身/目标效应 + 条件效应 + 自动衰减 + 延迟效应。

---

## 运维保护

- **暂停/恢复**：任意轮次可暂停，完整快照持久化（EntityState + 情感投资 + agent 演化数据 + 反思状态），重开应用可断点续写
- **轮级检查点**：每 3 轮自动写快照，防崩溃丢失全部进度
- **Token 预算预检**：启动前估判消耗，超额时告警建议减章
- **嵌入模型校验**：Phase 2 启动前快速校验嵌入模型可用性
- **蓝图完整性校验**：生成后检测必填字段（logline/key_events/characters），缺失告警
- **暂停快照隔离**：子系统序列化失败不会丢掉全部快照数据
- **反思批量调用**：多角色需要反思时合并为一次 LLM 调用（不再 N 次独立调用）

---

## 技术架构

- **后端**：Python 3.11 + FastAPI + uvicorn（`127.0.0.1:8760`）
- **前端**：Tauri 2（Rust 壳，系统托盘 + 自动拉起/关闭后端）+ React 18 + TypeScript + Vite + react-force-graph-3d / three.js
- **数据存储**：
  - SQLite — 会话 / 日志 / 报告 / token 统计 / 暂停快照
  - Kuzu — 人物关系图谱（实体/角色/事件/因果链）
  - LanceDB — 语义向量检索（原著切片 + 动态事件）
- **LLM 接入**：OpenAI 兼容接口，30+ 厂商注册表，对话与嵌入分别配置
- **桌面版数据**：`%LOCALAPPDATA%\LiteraryCreation\data\`

---

## 快速开始

```bash
# 后端
pip install -e .
python run.py                       # http://127.0.0.1:8760

# 前端
cd apps/literary-creation
npm install
npm run dev                         # Vite http://localhost:5173
npx tauri dev                       # 完整桌面应用
```

---

## 主要 API（前缀 `/api/forge`）

### 会话
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/upload` | 上传种子材料文件（txt/md/pdf/docx 等） |
| POST | `/session` | 创建会话（`config.total_rounds` 设章数、`config.target_words` 设总字数） |
| GET | `/sessions` | 会话列表 |
| DELETE | `/session/{id}` | 删除会话（连带清理 Kuzu/LanceDB） |

### 创作控制
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/session/{id}/start` | 启动创作 |
| POST | `/session/{id}/pause` | 暂停 |
| POST | `/session/{id}/resume` | 继续 |
| POST | `/session/{id}/intervene` | 实时干预 |
| POST | `/session/{id}/pre-goal` | 创作愿景 |
| POST | `/session/{id}/fsm-override` | 强制角色动作 |

### 数据查询
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/session/{id}/graph` | 人物关系图 |
| GET | `/session/{id}/timeline` | 情节时间线 |
| GET | `/session/{id}/causal` | 因果影响链 |
| GET | `/session/{id}/report` | 作品报告 |
| GET | `/session/{id}/logs` | 日志 |
| GET | `/session/{id}/tokens` | Token 统计 |
| GET | `/session/{id}/stream` | SSE 实时流 |

### 配置
| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/config/llm` | LLM 配置 |
| GET/POST | `/config/embedding` | 嵌入模型配置 |
| GET/POST | `/config/engine` | 引擎参数 |
| GET | `/config/providers` | 厂商列表 |
| POST | `/config/test-connection` | 连接测试 |
| POST | `/config/list-models` | 获取模型列表 |
| GET | `/domains` | 风格领域列表 |

---

## 环境变量

| 变量 | 说明 |
|------|------|
| `FORGE_LLM_BASE` | 对话模型 API 地址 |
| `FORGE_LLM_MODEL` | 对话模型 ID |
| `FORGE_LLM_KEY` | 对话模型 API Key |
| `FORGE_EMBED_BASE` | 嵌入模型 API 地址 |
| `FORGE_EMBED_MODEL` | 嵌入模型 ID |
| `FORGE_PROVIDER` | LLM 厂商 slug |
| `FORGE_DATA_DIR` | 数据目录（桌面版自动指向 `%LOCALAPPDATA%\LiteraryCreation\data`） |
| `FORGE_RULE_DIR` | 内置规则包目录 |
| `FORGE_MAX_CONCURRENT` | 并发 LLM 请求上限 |
| `FORGE_DEFAULT_ROUNDS` | 默认章数 |

---

## 项目结构

```
literarycreation/
├── src/literarycreation/
│   ├── core/                   # 配置 / LLM 客户端 / Provider 注册表 / Token 统计 / prompt 自适应
│   ├── engine/                 # 五阶段流水线 + 规则引擎 + 模拟器 + 散文渲染器
│   │   ├── emotional_engine.py    # 复合情感合成 + 投资追踪
│   │   ├── craft_guard.py         # 场景权重分配 + 母题培养
│   │   ├── narrator_broker.py     # 叙述者声音代理
│   │   ├── reader_model.py        # 读者体验模拟
│   │   ├── revision_pipeline.py   # 编辑修订流水线
│   │   ├── imagery_tracker.py     # 意象跨章追踪
│   │   ├── health_validator.py    # 嵌入模型/蓝图校验
│   │   └── ...
│   ├── storage/                # SQLite 会话库 + Kuzu 图库
│   └── api/                    # FastAPI 路由 + SSE 流
├── apps/literary-creation/     # Tauri 2 桌面应用
│   └── src-tauri/              # Rust 壳
├── data/rule/rules.json        # 5 种内置文学风格规则包
├── tests/ scripts/             # 测试与脚本
└── pyproject.toml
```

---

## 已知边界

| 擅长 | 不擅长 |
|------|--------|
| 情节驱动的中长篇现代小说（≤20 章） | ＞20 章时 `story_state` 累积文本稀释 prompt |
| 多角色群像悬疑/推理 | 单一主角深度心理小说 |
| 有明确终点的收束型故事 | 意识流/实验叙事 |
| 第三人称有限/全知 | 第一人称 |
| 5 种内置风格的纯类型 | 混合类型（科幻悬疑爱情） |
| 现代白话 | 古典话本/文言/诗词 |

---

## 许可证

AGPL-3.0-only
