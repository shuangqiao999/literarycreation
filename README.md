# LiteraryCreation — 文学创作助手

**把小说续写与剧本复现变成可计算、可复现、可优化、可导出的创作实验。**

LiteraryCreation 是一款本地优先的多智能体文学创作工具：以「一段小说开头」或「一份结构化提纲」为输入，自动构建人物关系图谱与角色人格，按"角色决策 → 情感/关系数值演化 → 反馈 → 情节推进"的闭环并行推演多轮，最终渲染为可导出的小说/剧本正文。它由 StrategyForge 的成熟推演架构剥离而来，专注创作场景，以「Python 后端 + Tauri 桌面应用」形态交付，可打包为独立安装包离线运行。

---

## 两大创作模式

### ✍️ Mode 1 · 半部小说续写
输入一部未完成的小说（如《红楼梦》前 80 回），系统自动提取人物、构建关系网、生成角色人格，推演出逻辑自洽、人物不 OOC 的后续情节，并渲染为续写正文。用户不提供结局约束，系统自行推演"最可能的发展路径"。

### 📋 Mode 2 · 提纲复现整部作品
输入结构化提纲（角色设定 + 弧光 + 关键事件 + 结局状态），系统按提纲复现完整文学文本：

- **按角色初值**：每个角色从提纲指定的初始状态（信任/张力/情感/权力…）出发。
- **弧光门控（软）**：将 `initial_state → final_state` 线性插值为每轮目标带，偏离超阈值时自动注入纠偏软提示。
- **事件门控（硬）**：提纲中的关键事件在指定轮次被强制推动（强制目标 + 高优先记忆注入）。
- **结局对齐**：报告输出各角色弧光达成度（`final_state` 误差量化）。

两种模式共用五阶段流水线，仅输入与约束方式不同。

---

## 核心特性

### 五阶段创作流水线
本体生成 → GraphRAG 人物关系图谱 → 角色人格工厂 → 并行推演 → 散文渲染，全程自动化。

### 文学量化引擎（内置 `literary` 规则包）
以 6 个文学指标驱动角色演化：`trust`（信任）/`tension`（张力）/`affection`（情感）/`power`（权力）/`mystery`（悬念）/`fatigue`（疲惫）。角色动作（`confront`/`confess`/`ally`/`betray`/`investigate`/`protect`/`manipulate`/`observe`）映射为结构化数值效应，含条件效应、延迟效应、自动衰减与有限状态机（中立/危机/亲密/背叛）。规则包内置、开箱即用，无需配置。

### 散文/剧本渲染器
Phase 5 将推演结果（角色最终状态 + 关键事件序列 + 原文风格参考）渲染为小说/剧本正文，含场景描写、人物对话、内心独白。支持叙事风格选择（现实主义 / 浪漫主义 / 悬疑 / 史诗 / 宫廷剧）。LLM 不可用时降级为结构化事件摘要，保证不中断。**支持一键导出为 UTF-8（含 BOM）`.txt`，Windows 记事本无乱码。**

### 多结局分支（蒙特卡洛）
对同一提纲/种子跑 N 次隔离推演，得到 N 个差异化结局，输出戏剧张力/契合度/成本对比与推荐分支，供创作择优。

### 创作控制
- **启动 / 暂停 / 继续**：任意轮次可暂停，进度持久化到 SQLite，重开应用可断点续写。
- **实时干预**：创作中注入指令改变剧情走向（如"让角色 A 在第 5 轮背叛角色 B"）。
- **创作愿景（pre-goal）**：为会话设定结局倾向，贯穿全轮次。
- **状态实时同步**：SSE 事件驱动，界面实时显示五阶段进度。

### 人物关系与情感因果（Kuzu）
人物关系网（RELATES，盟友/宿敌/亲属反哺决策）；情节时间线（Event/ACTED）；**情感因果链**（TARGETS/CAUSED，基于数值真值的精确归因，如「华妃 betray → 甄嬛 信任 −30」），驱动"情节脉络 / 人物影响链"可视化。

### 语义记忆与检索（LanceDB）
原著切片混合检索（向量 + 全文 FTS）+ 推演事件动态语义记忆 + 干预/目标显著性通道，带查询缓存。

### Token 统计
每次 LLM 调用输入/输出 token 无侵入自动记录（`contextvars`），按阶段/轮次汇总，前端汇总卡片 + SVG 柱状图。

### 桌面应用
Tauri 2 壳（系统托盘 + 自动拉起后端），React 18 前端含人物关系 3D 图、作品正文（可导出）、情节脉络/人物影响链、日志（实时 SSE）、Token 统计、多结局对比等视图，内置 LLM/嵌入模型配置页。

---

## 技术架构

- **后端**：Python 3.11 + FastAPI/uvicorn（`literarycreation.api:app`，默认 `http://127.0.0.1:8000`）。
- **桌面端**：Tauri 2（Rust 壳，系统托盘 + 自动拉起/关闭后端）+ React 18 + TypeScript + Vite + react-force-graph-3d / three.js。
- **数据存储**：
  - SQLite — 会话 / 日志 / 报告 / token 统计 / 暂停快照（`data/sessions.db`）
  - Kuzu — 人物关系与情感因果图（`data/graphs/{session}/kuzu`）
  - LanceDB — 向量/全文检索（`data/lancedb`）
  - `data/forge_config.json` — 端点与模型配置
- **LLM 接入**：OpenAI 兼容接口，统一 Provider 注册表，内置 28+ 厂商目录；对话与嵌入端点可分别配置。解析优先级：`forge_config.json` > `FORGE_*` 环境变量 > 厂商默认。
- **算法依赖**：`numpy` + `scipy`（文学域仅用离散规则引擎 + FSM，不加载 ODE/物理模块）。
- **许可证**：AGPL-3.0-only

> **数据隔离**：桌面版运行期数据写入 `%LOCALAPPDATA%\LiteraryCreation\data`，与 StrategyForge 互不干扰。环境变量沿用 `FORGE_` 前缀，兼容既有配置。

---

## 快速开始

### 1. 后端

```bash
# 安装（项目根目录 E:\gongxiang\literarycreation）
pip install -e .

# 配置：复制 .env.example 为 .env 并编辑（或在桌面应用「配置」页设置）
cp .env.example .env

# 启动开发服务器
python run.py
# 或：literary-creation serve
```

后端启动于 `http://127.0.0.1:8000`，文档 `http://127.0.0.1:8000/docs`，健康检查 `/health`。

### 2. 前端（桌面应用）

```bash
cd apps/literary-creation
npm install
npm run dev        # 仅前端 (Vite, http://localhost:5173)
# 或
npx tauri dev      # 完整桌面应用（自动构建并联调）
```

> 推荐本地用 [LM Studio](https://lmstudio.ai) 或 Ollama 提供对话与嵌入模型；也可在「配置」页填写任意云端 OpenAI 兼容服务商。长篇续写（如《红楼梦》级）建议使用 12B 以上模型或云端 API。

---

## 使用流程

1. **新建会话**：填入标题与叙事风格，选择输入模式：
   - **✍️ 种子续写**：粘贴小说开头/种子文本。
   - **📋 提纲复现**：填写角色表（名称 / 弧光 / 初值·终值）与关键事件表（轮次 / 事件），章节数即推演轮数。
2. **（可选）**：填写创作愿景（pre-goal）。
3. **运行**：
   - **「开始创作」**：跑一次完整五阶段推演；运行中可停止。
   - **「继续」**：从上次暂停轮次恢复。
   - **「多结局」**：对同一输入做蒙特卡洛，输出多个结局分支。
4. **查看**：
   - **作品**：文学正文（可折叠）+ **⬇ 导出 TXT**；提纲模式额外显示弧光达成度。
   - **人物关系**：3D 关系网（盟友/宿敌/亲属）。
   - **情节脉络 / 人物影响链**：谁先做什么、对谁造成什么情感后果；点击节点查看关联文本。
   - **日志**：实时 SSE。
   - **Token**：汇总卡片 + 柱状图。
5. **干预**：创作中在底部输入框发送指令，改变剧情走向。

---

## 环境变量

所有带 `FORGE_` 前缀的环境变量均有 `data/forge_config.json` 覆盖机制。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `FORGE_LLM_BASE` | `http://127.0.0.1:1234/v1` | 对话模型 API 地址 |
| `FORGE_LLM_KEY` | `lm-studio` | 对话模型 API Key |
| `FORGE_LLM_MODEL` | `qwen/qwen3.5-9b` | 对话模型 ID |
| `FORGE_EMBED_BASE` | 同 LLM | 嵌入模型 API 地址 |
| `FORGE_EMBED_MODEL` | `text-embedding-embeddinggemma-300m-qat` | 嵌入模型 ID |
| `FORGE_PROVIDER` | （空） | 默认厂商标识（如 `lmstudio` / `openai`） |
| `FORGE_MAX_AGENTS` | `10000` | 最大角色数 |
| `FORGE_DEFAULT_ROUNDS` | `10` | 默认推演轮数（章节数） |
| `FORGE_MAX_CONCURRENT` | `2` | 并发 LLM 请求上限 |
| `FORGE_RETRIEVE_TOP_K` | `5` | LanceDB 检索返回 Top-K |
| `FORGE_SIMILARITY_THRESHOLD` | `0.4` | 语义检索相似度阈值 |
| `FORGE_DATA_DIR` | `./data` | 运行期数据目录（桌面版：`%LOCALAPPDATA%\LiteraryCreation\data`） |
| `FORGE_RULE_DIR` | （空） | 内置规则包目录（桌面打包时由 Tauri 壳设置） |

> 以上默认值为 `core/config.py` 中 `DeductionConfig` 的硬编码值；`.env.example` 仅作示例。

---

## `literary` 规则包

文学量化的核心驱动组件，将角色的离散决策映射为结构化情感/关系数值效应。内置于 `data/rule/rules.json` 的 `literary` 域，无需用户配置。

```json
{
  "domain": "literary",
  "display_name": "文学叙事",
  "metrics": ["trust", "tension", "affection", "power", "mystery", "fatigue"],
  "thresholds": {},
  "actions": ["confront", "confess", "ally", "betray", "investigate", "protect", "manipulate", "observe"],
  "self_effects": { "betray": {"trust": -40, "tension": 30, "affection": -30, "power": 10} },
  "target_effects": { "betray": {"trust": -30, "tension": 20, "affection": -20} },
  "conditional_effects": { "confess_risky": {"condition": "affection > 60 and trust > 40", "self_effects": {"affection": 25}} },
  "auto_effects": { "tension_decay": {"condition": "tension > 40", "effects": {"tension": -3, "trust": 1}} },
  "modules": {
    "pipeline": { "order": ["outline_control", "finite_state_machine"] },
    "outline_control": { "deviation_threshold": 12.0, "catch_up_window": 2 },
    "finite_state_machine": { "default_state": "neutral", "command_states": ["crisis"], "...": "..." }
  }
}
```

- `thresholds` 置空：角色不因数值被自动"淘汰"，去留由剧情决定。
- `outline_control`：Mode 2 弧光门控模块（`algorithms/outline_control.py`）。
- 无 ODE/物理模块：文学指标为离散演化。

> 保留的 8 个战略领域规则包（军事/商业/政治/生态/城市/科技/信息战/地缘）仍在 `rules.json` 中，但前端锁定为文学叙事。

---

## 主要 API（前缀 `/api/forge`）

### 会话
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/upload` | 上传种子材料文件 |
| POST | `/session` | 创建会话（`config.outline` 传提纲、`config.style` 传风格） |
| GET | `/sessions` | 会话列表 |
| GET | `/session/{id}` | 会话详情 |
| DELETE | `/session/{id}` | 删除会话（连带清理 Kuzu/LanceDB） |

### 创作控制
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/session/{id}/start` | 启动创作（新建或从暂停恢复） |
| POST | `/session/{id}/pause` / `/resume` | 暂停 / 继续 |
| POST | `/session/{id}/intervene` | 实时干预（改变剧情走向） |
| POST | `/session/{id}/pre-goal` | 设定创作愿景 |
| POST | `/session/{id}/settings` | 会话级设置 |

### 数据查询
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/session/{id}/graph` | 人物关系图谱 |
| GET | `/session/{id}/timeline` | 情节时间线 |
| GET | `/session/{id}/causal` | 人物影响链（TARGETS/CAUSED） |
| GET | `/session/{id}/report` | 作品报告（含 `prose` / `arc_alignment`） |
| GET | `/session/{id}/logs` | 会话日志 |
| GET | `/session/{id}/tokens` | Token 统计 |
| GET | `/session/{id}/stream` | SSE 实时事件流 |

### 多结局 / 配置
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/session/{id}/optimize` | 启动多结局蒙特卡洛 |
| GET | `/session/{id}/optimize/result` | 进度与结果轮询 |
| GET/POST | `/config/llm` · `/config/embedding` | 端点与模型配置 |
| POST | `/config/test-connection` | 测试端点连通性 |

---

## 项目结构

```
literarycreation/
├── src/literarycreation/
│   ├── algorithms/       # 通用算法模块（FSM / ODE / Physics / outline_control🆕 / 模块链工厂）
│   ├── core/             # 配置 / Provider 注册表 / LLM 适配 / Token 统计 / 分块器
│   ├── engine/           # 五阶段流水线 + 规则引擎 + 优化器 + prose_renderer🆕 + 预处理器 + 推理器
│   ├── storage/          # SQLite 会话库 + Kuzu 图库
│   └── api/              # FastAPI 路由 + SSE 事件流 + 配置路由
├── apps/literary-creation/       # Tauri 2 桌面应用（React 18 + 3D 图）
│   └── src-tauri/                # Rust 壳（子进程管理 + 系统托盘 + NSIS 打包）
├── data/rule/rules.json          # 规则包（含 literary 域）
├── scripts/  tests/              # 测试脚本与用例
├── pyproject.toml  run.py
└── literary-creation-backend.spec  # PyInstaller 打包配置
```

---

## 打包与发布（Windows）

```bash
# 后端（onedir）
python -m PyInstaller literary-creation-backend.spec --noconfirm

# 前端 + Tauri 桌面安装包
cd apps/literary-creation
npm run build
npx tauri build --bundles nsis
```

产物：
- 应用：`apps/literary-creation/src-tauri/target/release/literary-creation.exe`
- 安装包：`.../bundle/nsis/LiteraryCreation_0.1.0_x64-setup.exe`
- 后端：`dist/literary-creation-backend/`

---

## 致谢

LiteraryCreation 由 **StrategyForge**（多智能体战略推演引擎）剥离改造而来，复用其五阶段流水线、规则引擎、FSM、GraphRAG、蒙特卡洛优化器与桌面外壳，聚焦文学创作场景。
