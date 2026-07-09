import { useState, useEffect, useCallback, useRef } from "react";
import ForceGraph3D from "react-force-graph-3d";

const API_BASE = import.meta.env.DEV ? "/api/forge" : "http://127.0.0.1:8760/api/forge";

const lbl: React.CSSProperties = { fontSize: 13, color: "#94a3b8", marginBottom: 4, display: "block" };
const inp: React.CSSProperties = { height: 32, marginBottom: 8, width: "100%" };
const btn: React.CSSProperties = { height: 28, fontSize: 13, borderRadius: 6, border: "1px solid #334155", cursor: "pointer", padding: "0 12px" };

// ── Types ──

interface SessionItem {
  id: string;
  title: string;
  status: string;
  phase: string;
  entity_count: number;
  relation_count: number;
  agent_count: number;
  current_round: number;
  total_rounds: number;
  created_at: string;
}

interface GraphData {
  nodes: Array<{ id: string; name: string; type: string; description: string }>;
  links: Array<{ source: string; target: string; relation: string; weight: number }>;
}

interface LogEntry {
  phase: string;
  message: string;
  timestamp: string;
}

interface TimelineAction { action: string; timestamp: string; description: string; event_type: string; effect?: string; driver?: string; }
interface AgentTimeline { agent_id: string; agent_name: string; actions: TimelineAction[]; }
interface TimelineData {
  timelines: AgentTimeline[];
  sequence: Array<{ timestamp: string; agent_name: string; action: string; description: string; event_type: string; effect?: string; driver?: string }>;
}

interface CausalNode { id: string; kind: string; label: string; desc?: string; }
interface CausalLink { source: string; target: string; type: string; label: string; }
interface CausalData {
  nodes: CausalNode[];
  links: CausalLink[];
  summary: Array<{ source: string; target: string; metric: string; amount: number }>;
}

interface ReportData {
  summary?: string;
  key_events?: Array<any>;
  risk_alerts?: string[];
  recommendations?: string[];
  quantified?: boolean;
  domain?: string;
  final_states?: Record<string, { name: string; metrics: Record<string, number>; history?: any[]; alive: boolean }>;
  causal_summary?: string[];
  stage_narratives?: Array<{ stage: string; round_range: string; start_state: string; key_decisions: string; causal_logic: string; end_state: string }>;
  deviation_analysis?: Array<{ round: number; agent: string; decision: string; deviation_level: string; reason: string }>;
  conclusion?: string;
  is_literary?: boolean;
  prose?: string;
  style?: string;
  arc_alignment?: Array<{ name: string; win_score: number; final_metrics: Record<string, number>; target: Record<string, number> }>;
  key_events_plan?: Array<{ round: number; event: string }>;
  chapters?: Array<{ index: number; title: string; file: string; words: number }>;
  work_dir?: string;
  target_words?: number;
}

interface TokenStats {
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  phases: Record<string, { prompt: number; completion: number; total: number }>;
  rounds: Record<string, { prompt: number; completion: number; total: number }>;
}

// ── Phase Labels ──

const PHASE_LABELS: Record<string, string> = {
  created: "已创建",
  ontology_running: "本体生成中...",
  graph_running: "图谱构建中...",
  agents_running: "智能体生成中...",
  simulating: "模拟推演中...",
  reporting: "报告生成中...",
  optimizing: "多结局生成中...",
  complete: "已完成",
  failed: "失败",
  paused: "已暂停",
};
const RUNNING_SET = new Set(["ontology_running","graph_running","agents_running","simulating","reporting","optimizing"]);

const Toggle = ({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) => (
  <label style={{ display: "inline-flex", alignItems: "center", flexShrink: 0, cursor: "pointer" }}>
    <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)}
      style={{ position: "absolute", opacity: 0, width: 0, height: 0 }} />
    <span style={{
      position: "relative", display: "inline-block", width: 36, height: 20,
      borderRadius: 10, background: checked ? "#22c55e" : "#475569",
      transition: "background 0.2s ease",
    }}>
      <span style={{
        position: "absolute", top: 2, left: checked ? 18 : 2,
        width: 16, height: 16, borderRadius: "50%", background: "#fff",
        transition: "left 0.2s ease", boxShadow: "0 1px 3px rgba(0,0,0,0.3)",
      }} />
    </span>
  </label>
);

// ── Main App ──

export default function App() {
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [sourceMaterial, setSourceMaterial] = useState("");
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [report, setReport] = useState<ReportData | null>(null);
  const [timeline, setTimeline] = useState<TimelineData | null>(null);
  const [causal, setCausal] = useState<CausalData | null>(null);
  const [tokenData, setTokenData] = useState<TokenStats | null>(null);
  const [timelineView, setTimelineView] = useState<"timeline" | "causal">("timeline");
  const [mainTab, setMainTab] = useState<"graph" | "report" | "logs" | "timeline" | "token" | "dashboard">("graph");
  const [domain, setDomain] = useState("literary_realism");
  const [domains, setDomains] = useState<Array<{domain:string;name:string}>>([]);

  // ── 文学创作（Mode 1 续写 / Mode 2 提纲复现）──
  const [inputMode, setInputMode] = useState<"seed" | "outline">("seed");
  const [style, setStyle] = useState("");
  const [chapters, setChapters] = useState(10);
  const [targetWords, setTargetWords] = useState(100000);
  const LIT_METRICS = ["trust", "tension", "affection", "power", "mystery", "fatigue"] as const;
  const LIT_METRIC_CN: Record<string, string> = { trust: "信任", tension: "张力", affection: "情感", power: "权力", mystery: "悬念", fatigue: "疲惫" };
  type OutlineChar = { name: string; arc: string; initial: Record<string, string>; final: Record<string, string> };
  type OutlineEvent = { round: number; event: string };
  const [characters, setCharacters] = useState<OutlineChar[]>([{ name: "", arc: "", initial: {}, final: {} }]);
  const [keyEvents, setKeyEvents] = useState<OutlineEvent[]>([{ round: 1, event: "" }]);
  const [selectedCausalNode, setSelectedCausalNode] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [preGoal, setPreGoal] = useState("");
  const [interventionText, setInterventionText] = useState("");
  const [sending, setSending] = useState(false);
  const [ovAgent, setOvAgent] = useState<string | null>(null);
  const [ovAction, setOvAction] = useState("");
  const [ovIntensity, setOvIntensity] = useState(0.6);
  const [ovTarget, setOvTarget] = useState("");
  const [ovRounds, setOvRounds] = useState(1);
  const logsRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const graphRef = useRef<any>(null);
  const causalGraphRef = useRef<any>(null);
  const sseSidRef = useRef<string | null>(null);

  const zoomGraph = (rf: React.RefObject<any>, factor: number) => {
    const fg = rf.current; if (!fg) return;
    const pos = fg.cameraPosition();
    fg.cameraPosition({ x: pos.x * factor, y: pos.y * factor, z: pos.z * factor }, { x: 0, y: 0, z: 0 } as any, 300);
  };
  const resetGraph = (rf: React.RefObject<any>) => { rf.current?.zoomToFit(400, 50); };

  // ── Settings ──
  const [showSettings, setShowSettings] = useState(false);
  const [settingsTab, setSettingsTab] = useState<"llm" | "embed" | "engine">("llm");
  const [cfgLLMBase, setCfgLLMBase] = useState("");
  const [cfgLLMKey, setCfgLLMKey] = useState("");
  const [cfgLLMModel, setCfgLLMModel] = useState("");
  const [cfgLLMProvider, setCfgLLMProvider] = useState("");
  const [cfgLLMTemp, setCfgLLMTemp] = useState(0.3);
  const [cfgEmbedBase, setCfgEmbedBase] = useState("");
  const [cfgEmbedKey, setCfgEmbedKey] = useState("");
  const [cfgEmbedModel, setCfgEmbedModel] = useState("");
  const [cfgEmbedProvider, setCfgEmbedProvider] = useState("");
  const [cfgFetchingModels, setCfgFetchingModels] = useState(false);
  const [cfgLLMFetchedModels, setCfgLLMFetchedModels] = useState<string[]>([]);
  const [cfgLLMModelError, setCfgLLMModelError] = useState("");
  const [cfgEmbedFetchedModels, setCfgEmbedFetchedModels] = useState<string[]>([]);
  const [cfgEmbedModelError, setCfgEmbedModelError] = useState("");
  const [cfgSaving, setCfgSaving] = useState(false);
  const [cfgLLMTest, setCfgLLMTest] = useState<"" | "testing" | "ok" | "fail">("");
  const [cfgProviders, setCfgProviders] = useState<Array<{slug:string;name:string;default_llm_base_url:string;default_llm_model:string;default_embed_model:string;note:string}>>([]);

  const EMBED_MODEL_KW = ["embed", "embedding", "bge", "e5", "gte", "stella", "nomic", "jina"];

  // ── 引擎配置 ──
  const [cfgMaxAgents, setCfgMaxAgents] = useState(10000);
  const [cfgMaxConcurrent, setCfgMaxConcurrent] = useState(2);
  const [cfgRetrieveTopK, setCfgRetrieveTopK] = useState(5);

  const fetchConfig = useCallback(async (): Promise<boolean> => {
    try {
      const [lr, er, pr] = await Promise.all([
        fetch(`${API_BASE}/config/llm`).then(r => r.ok ? r.json() : null),
        fetch(`${API_BASE}/config/embedding`).then(r => r.ok ? r.json() : null),
        fetch(`${API_BASE}/config/providers`).then(r => r.ok ? r.json() : null),
      ]);
      if (lr) { setCfgLLMBase(lr.llm_base_url || ""); setCfgLLMKey(lr.llm_api_key || ""); setCfgLLMModel(lr.llm_model || ""); setCfgLLMProvider(lr.provider_slug || ""); setCfgLLMTemp(lr.llm_temperature || 0.3); }
      if (er) { setCfgEmbedBase(er.embedding_api_base || ""); setCfgEmbedKey(er.embedding_api_key || ""); setCfgEmbedModel(er.embedding_model_name || ""); setCfgEmbedProvider(er.provider_slug || ""); }
      if (pr) { setCfgProviders(pr.providers || []); }
      const eng = await fetch(`${API_BASE}/config/engine`).then(r => r.ok ? r.json() : null);
      if (eng) {
        setCfgMaxAgents(eng.max_agents ?? 10000);
        setCfgMaxConcurrent(eng.max_concurrent ?? 2);
        setCfgRetrieveTopK(eng.retrieve_top_k ?? 5);
      }
      return !!(pr && (pr.providers || []).length);
    } catch { return false; }
  }, []);

  // 后端 exe 由 Tauri 启动需数秒就绪，挂载时轮询重试直到服务商加载成功。
  useEffect(() => {
    let cancelled = false;
    let attempts = 0;
    const tick = async () => {
      if (cancelled) return;
      const ok = await fetchConfig();
      attempts += 1;
      if (!ok && attempts < 20 && !cancelled) setTimeout(tick, 1500);
    };
    tick();
    return () => { cancelled = true; };
  }, [fetchConfig]);

  // 动态加载规则包领域列表（内置 + 自定义），解耦前端硬编码
  useEffect(() => { fetchDomains(); }, []);

  const fetchModels = useCallback(async () => {
    const base = settingsTab === "llm" ? cfgLLMBase : cfgEmbedBase;
    const key = settingsTab === "llm" ? cfgLLMKey : cfgEmbedKey;
    if (!base) return;
    setCfgFetchingModels(true);
    if (settingsTab === "llm") { setCfgLLMModelError(""); setCfgLLMFetchedModels([]); }
    else { setCfgEmbedModelError(""); setCfgEmbedFetchedModels([]); }
    try {
      const r = await fetch(`${API_BASE}/config/list-models`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_type: "openai", base_url: base, api_key: key }),
      });
      const data = await r.json();
      const all: string[] = data.models || [];
      if (data.error) {
        if (settingsTab === "llm") setCfgLLMModelError(data.error);
        else setCfgEmbedModelError(data.error);
      } else if (settingsTab === "llm") {
        // LLM tab: 过滤掉嵌入模型
        const filtered = all.filter(m => !EMBED_MODEL_KW.some(k => m.toLowerCase().includes(k)));
        setCfgLLMFetchedModels(filtered);
        if (filtered.length === 0) setCfgLLMModelError("未检测到对话模型（已过滤嵌入模型），请检查模型列表");
      } else {
        // Embedding tab: 仅保留嵌入模型，若全过滤则留全部
        const filtered = all.filter(m => EMBED_MODEL_KW.some(k => m.toLowerCase().includes(k)));
        setCfgEmbedFetchedModels(filtered.length > 0 ? filtered : all);
      }
    } catch (e: any) {
      if (settingsTab === "llm") setCfgLLMModelError(e.message || "Failed");
      else setCfgEmbedModelError(e.message || "Failed");
    }
    setCfgFetchingModels(false);
  }, [settingsTab, cfgLLMBase, cfgLLMKey, cfgEmbedBase, cfgEmbedKey]);

  const testLLM = useCallback(async () => {
    setCfgLLMTest("testing");
    try {
      const r = await fetch(`${API_BASE}/config/test-connection`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_type: "openai", base_url: cfgLLMBase, api_key: cfgLLMKey }),
      });
      const data = await r.json();
      setCfgLLMTest(data.ok ? "ok" : "fail");
    } catch { setCfgLLMTest("fail"); }
  }, [cfgLLMBase, cfgLLMKey]);

  const saveConfig = useCallback(async () => {
    setCfgSaving(true);
    try {
      await fetch(`${API_BASE}/config/llm`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ llm_base_url: cfgLLMBase, llm_api_key: cfgLLMKey, llm_model: cfgLLMModel, provider_slug: cfgLLMProvider, llm_temperature: cfgLLMTemp }),
      });
      await fetch(`${API_BASE}/config/embedding`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ embedding_api_base: cfgEmbedBase, embedding_api_key: cfgEmbedKey, embedding_model_name: cfgEmbedModel, provider_slug: cfgEmbedProvider }),
      });
      await fetch(`${API_BASE}/config/engine`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ max_agents: cfgMaxAgents, max_concurrent: cfgMaxConcurrent, retrieve_top_k: cfgRetrieveTopK }),
      });
      await fetchConfig();
      setShowSettings(false);
    } catch { /* ignore */ }
    setCfgSaving(false);
  }, [cfgLLMBase, cfgLLMKey, cfgLLMModel, cfgLLMProvider, cfgLLMTemp, cfgEmbedBase, cfgEmbedKey, cfgEmbedModel, cfgEmbedProvider,
      cfgMaxAgents, cfgMaxConcurrent, cfgRetrieveTopK, fetchConfig]);

  const fetchSessions = useCallback(async (): Promise<boolean> => {
    try {
      const r = await fetch(`${API_BASE}/sessions`);
      if (r.ok) { setSessions(await r.json()); return true; }
      return false;
    } catch { return false; }
  }, []);

  // 冷启动时后端可能尚未就绪，轮询重试直到会话列表加载成功。
  useEffect(() => {
    let cancelled = false;
    let attempts = 0;
    const tick = async () => {
      if (cancelled) return;
      const ok = await fetchSessions();
      attempts += 1;
      if (!ok && attempts < 20 && !cancelled) setTimeout(tick, 1500);
    };
    tick();
    return () => { cancelled = true; };
  }, [fetchSessions]);

  const fetchGraph = useCallback(async (sessionId: string) => {
    try {
      const r = await fetch(`${API_BASE}/session/${sessionId}/graph`);
      if (r.ok) setGraphData(await r.json());
    } catch { /* ignore */ }
  }, []);

  const fetchTimeline = useCallback(async (sessionId: string) => {
    try {
      const r = await fetch(`${API_BASE}/session/${sessionId}/timeline`);
      if (r.ok) setTimeline(await r.json());
    } catch { /* ignore */ }
  }, []);

  const fetchCausal = useCallback(async (sessionId: string) => {
    try {
      const r = await fetch(`${API_BASE}/session/${sessionId}/causal`);
      if (r.ok) setCausal(await r.json());
    } catch { /* ignore */ }
  }, []);

  const fetchTokens = useCallback(async (sessionId: string) => {
    try {
      const r = await fetch(`${API_BASE}/session/${sessionId}/tokens`);
      if (r.ok) {
        const d = await r.json();
        setTokenData(d.stats && Object.keys(d.stats).length > 0 ? d.stats : null);
      }
    } catch (e: any) { console.error("Token fetch failed:", e.message); }
  }, []);

  const fetchDomains = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/domains`);
      if (r.ok) setDomains((await r.json()).domains || []);
    } catch { /* ignore */ }
  }, []);

  const fetchLogs = useCallback(async (sessionId: string) => {
    try {
      const r = await fetch(`${API_BASE}/session/${sessionId}/logs`);
      if (r.ok) setLogs(await r.json());
    } catch { /* ignore */ }
  }, []);

  const fetchReport = useCallback(async (sessionId: string) => {
    try {
      const r = await fetch(`${API_BASE}/session/${sessionId}/report`);
      if (r.ok) {
        const d = await r.json();
        const rep = d.report || null;
        setReport(rep && Object.keys(rep).length ? rep : null);
      } else setReport(null);
    } catch { setReport(null); }
  }, []);

  const selectSession = useCallback((id: string) => {
    setSelectedId(id);
    setMainTab("graph");
    fetchGraph(id);
    fetchLogs(id);
    fetchReport(id);
    fetchTimeline(id);
    fetchCausal(id);
    fetchTokens(id);
  }, [fetchGraph, fetchLogs, fetchReport, fetchTimeline, fetchCausal]);

  const handleCreate = useCallback(async () => {
    if (!title.trim()) { alert("请输入会话标题"); return; }
    // Mode 1 需种子文本；Mode 2 需至少一个有效角色
    const validChars = characters.filter(c => c.name.trim());
    if (inputMode === "seed" && !sourceMaterial.trim()) { alert("请输入小说开头/种子文本"); return; }
    if (inputMode === "outline") {
      const names = validChars.map(c => c.name.trim());
      if (names.length === 0) { alert("提纲模式请至少添加一个角色"); return; }
      if (new Set(names).size !== names.length) { alert("角色名不能重复"); return; }
      for (const e of keyEvents) {
        if (e.event.trim() && (e.round < 1 || e.round > chapters)) { alert(`事件轮次需在 1..${chapters} 之间`); return; }
      }
    }
    setCreating(true);
    try {
      const config: any = { domain, target_words: targetWords };
      if (inputMode === "outline") {
        const toNum = (o: Record<string, string>) => {
          const r: Record<string, number> = {};
          for (const k of LIT_METRICS) { const v = o[k]; if (v !== undefined && v !== "") r[k] = Number(v); }
          return r;
        };
        config.outline = {
          characters: validChars.map(c => ({ name: c.name.trim(), arc: c.arc.trim(),
            initial_state: toNum(c.initial), final_state: toNum(c.final) })),
          key_events: keyEvents.filter(e => e.event.trim()).map(e => ({ round: e.round, event: e.event.trim() })),
          chapters,
        };
      }
      const r = await fetch(`${API_BASE}/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, source_material: sourceMaterial, total_rounds: chapters, config }),
      });
      if (r.ok) {
        const data = await r.json();
        setSelectedId(data.id);
        setSessions(prev => [{ id: data.id, title: data.title, status: data.status, phase: "", entity_count: 0, relation_count: 0, agent_count: 0, current_round: 0, total_rounds: chapters, created_at: data.created_at }, ...prev]);
      }
    } catch { /* ignore */ }
    setCreating(false);
  }, [title, sourceMaterial, inputMode, chapters, targetWords, characters, keyEvents, domain]);

  const handleFileUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const ext = file.name.split(".").pop()?.toLowerCase();
    const allowed = ["txt","md","json","pdf","docx","py","js","ts","rs","go","java","c","cpp","h","csv","log","yaml","yml"];
    if (!ext || !allowed.includes(ext)) {
      e.target.value = "";
      return;
    }
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch(`${API_BASE}/upload`, { method: "POST", body: fd });
      if (!r.ok) { const err = await r.text(); throw new Error(err); }
      const data = await r.json();
      setSourceMaterial(data.text_content);
      const titleHint = file.name.replace(/\.[^.]+$/, "").slice(0, 40);
      if (!title.trim()) setTitle(titleHint);
    } catch (err: any) {
      alert("文件上传失败: " + (err.message || "未知错误"));
    }
    setUploading(false);
    e.target.value = "";
  }, [title]);

  const handleStart = useCallback(async () => {
    if (!selectedId) return;
    setLoading(true);
    setLogs([]);
    try {
      const r = await fetch(`${API_BASE}/session/${selectedId}/start`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      await fetchSessions();
    } catch (e: any) {
      alert("推演启动失败: " + (e.message || "未知错误"));
    }
    setLoading(false);
  }, [selectedId, fetchSessions]);

  const handleCancel = useCallback(async () => {
    if (!selectedId) return;
    setLoading(true);
    try {
      const r = await fetch(`${API_BASE}/session/${selectedId}/pause`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
    } catch (e: any) {
      alert("停止推演失败: " + (e.message || "未知错误"));
    }
    setLoading(false);
    await fetchSessions();
  }, [selectedId, fetchSessions]);

  const handleResume = useCallback(async () => {
    if (!selectedId) return;
    setLoading(true);
    setLogs([]);
    try {
      const r = await fetch(`${API_BASE}/session/${selectedId}/resume`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      setLoading(false);
      await fetchSessions();
    } catch (e: any) {
      setLoading(false);
      alert("继续推演失败: " + (e.message || "未知错误"));
    }
    setLoading(false);
  }, [selectedId, fetchSessions]);

  const handleDelete = useCallback(async (id: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    if (!window.confirm("确定删除该推演记录？将同时清除图谱、向量库与会话数据，且不可恢复。")) return;
    try {
      const r = await fetch(`${API_BASE}/session/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      if (selectedId === id) { setSelectedId(null); setGraphData(null); setLogs([]); setReport(null); }
      fetchSessions();
    } catch (err: any) {
      alert("删除失败: " + (err.message || "未知错误"));
    }
  }, [selectedId, fetchSessions]);

  const sendPreGoal = useCallback(async () => {
    if (!selectedId || !preGoal.trim()) return;
    try {
      await fetch(`${API_BASE}/session/${selectedId}/pre-goal`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: preGoal }),
      });
      setPreGoal("");
    } catch { /* ignore */ }
  }, [selectedId, preGoal]);

  const sendIntervention = useCallback(async () => {
    if (!selectedId || !interventionText.trim()) return;
    setSending(true);
    try {
      await fetch(`${API_BASE}/session/${selectedId}/intervene`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: interventionText, scope: "during" }),
      });
      setInterventionText("");
      await fetchLogs(selectedId);
    } catch (err: any) {
      alert("干预发送失败: " + (err.message || "未知错误"));
    }
    setSending(false);
  }, [selectedId, interventionText, fetchLogs]);

  const submitFsmOverride = useCallback(async (agent: string) => {
    if (!selectedId || !agent || !ovAction.trim()) { alert("请填写强制动作"); return; }
    try {
      const r = await fetch(`${API_BASE}/session/${selectedId}/fsm-override`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent, action_type: ovAction.trim(), intensity: ovIntensity, target: ovTarget.trim(), rounds: ovRounds }),
      });
      if (r.ok) { setOvAgent(null); setOvAction(""); setOvTarget(""); await fetchLogs(selectedId); }
      else alert("强制失败: " + (await r.text()));
    } catch (err: any) {
      alert("强制发送失败: " + (err.message || "未知错误"));
    }
  }, [selectedId, ovAction, ovIntensity, ovTarget, ovRounds, fetchLogs]);

  // SSE auto-refresh logs, graph, timeline during ALL running phases
  useEffect(() => {
    if (!selectedId) return;
    const selected = sessions.find(s => s.id === selectedId);
    if (!selected) return;
    const runningSet = new Set(["ontology_running","graph_running","agents_running","simulating","reporting","optimizing"]);
    if (!runningSet.has(selected.status)) { sseSidRef.current = null; return; }
    if (sseSidRef.current === selectedId) return; // already connected
    sseSidRef.current = selectedId;
    const es = new EventSource(`${API_BASE}/session/${selectedId}/stream`);
    es.onmessage = (ev: MessageEvent) => {
      if (ev.data === "[DONE]") { es.close(); sseSidRef.current = null; fetchSessions(); fetchGraph(selectedId); fetchReport(selectedId); fetchTimeline(selectedId); fetchCausal(selectedId); fetchTokens(selectedId); return; }
      try {
        const d = JSON.parse(ev.data);
        if (d.type === "round") {
          if (d.snapshot) setSnapshot(d.snapshot);
          fetchGraph(selectedId);
          fetchTimeline(selectedId);
          fetchCausal(selectedId);
          fetchTokens(selectedId);
        } else if (d.type === "status") {
          if (["complete","failed"].includes(d.status)) return; // [DONE] will handle terminal status
          fetchSessions();
        } else if (d.type === "error") {
          // ignore
        } else {
          setLogs(prev => [...prev, { phase: d.phase || "", message: d.message || "", timestamp: d.timestamp || "" }]);
          if (logsRef.current) logsRef.current.scrollTop = logsRef.current.scrollHeight;
        }
      } catch { /* ignore */ }
    };
    es.onerror = () => { es.close(); sseSidRef.current = null; fetchSessions(); };
    return () => { if (sseSidRef.current !== selectedId) es.close(); };  // do NOT clear sseSidRef here; session still connected
  }, [selectedId, sessions]);

  // Token 统计：前 3 次每 10 秒拉取，之后每 2 分钟
  useEffect(() => {
    if (!selectedId) return;
    let count = 0;
    let timer: number;
    const runningSet = new Set(["ontology_running","graph_running","agents_running","simulating","reporting","optimizing"]);
    const tick = () => {
      const sel = sessions.find(s => s.id === selectedId);
      if (sel && runningSet.has(sel.status)) {
        fetchTokens(selectedId);
      }
      count++;
      const delay = count < 3 ? 10000 : 120000;
      timer = window.setTimeout(tick, delay);
    };
    tick();
    return () => window.clearTimeout(timer);
  }, [selectedId, sessions, fetchTokens]);

  // 切换到 Token tab 时立即拉取
  useEffect(() => {
    if (mainTab === "token" && selectedId) {
      fetchTokens(selectedId);
    }
  }, [mainTab, selectedId, fetchTokens]);

  const selected = sessions.find(s => s.id === selectedId);

  const exportProseTxt = useCallback(() => {
    if (!report?.prose) return;
    const body = [(selected?.title || title || "未命名作品"), "", report.prose].join("\r\n");
    const blob = new Blob(["\uFEFF" + body], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const safe = (selected?.title || title || "literary_work").replace(/[\\/:*?"<>|]/g, "_");
    a.href = url; a.download = `${safe}.txt`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }, [report, selected, title]);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", height: "100%", overflow: "hidden" }}>
      {/* ── Left Panel: Sessions ── */}
      <div style={{ borderRight: "1px solid #374151", overflow: "auto", padding: 12 }}>
        <h3 style={{ margin: "0 0 8px", fontSize: 15, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span>📖 LiteraryCreation 文学创作</span>
          <button onClick={() => { fetchConfig(); setShowSettings(true); }} style={{ background: "#334155", border: "1px solid #475569", color: "#e2e8f0", borderRadius: 6, padding: "2px 8px", cursor: "pointer", fontSize: 12 }}>⚙ 配置</button>
        </h3>

        <div className="card" style={{ marginBottom: 10 }}>
          <input
            style={{ height: 32, marginBottom: 6, width: "100%" }}
            placeholder="会话标题"
            value={title}
            onChange={e => setTitle(e.target.value)}
          />
          <textarea
            style={{ height: 100, fontSize: 13, marginBottom: 6, width: "100%" }}
            placeholder={inputMode === "seed" ? "粘贴小说开头/种子文本（或点击上传文档）" : "原文风格参考（可选，用于文笔与语气）"}
            value={sourceMaterial}
            onChange={e => setSourceMaterial(e.target.value)}
          />
          <textarea
            style={{ height: 48, fontSize: 13, marginBottom: 6, width: "100%" }}
            placeholder="创作愿景/结局倾向（可选）"
            value={preGoal}
            onChange={e => setPreGoal(e.target.value)}
          />
          <select
            value={domain}
            onChange={e => setDomain(e.target.value)}
            title="叙事风格"
            style={{ height: 32, marginBottom: 6, width: "100%", background: "#1e293b", color: "#e2e8f0", border: "1px solid #334155", borderRadius: 6, fontSize: 13 }}
          >
            <option value="auto">🔍 自动（按素材判定风格）</option>
            {domains.length > 0
              ? domains.map(d => <option key={d.domain} value={d.domain}>{d.name}</option>)
              : [
                ["literary_realism", "现实主义"], ["literary_romance", "浪漫主义"],
                ["literary_suspense", "悬疑"], ["literary_epic", "史诗"], ["literary_court", "宫廷剧"],
              ].map(([key, label]) => <option key={key} value={key}>🎨 {label}</option>)
            }
          </select>
          <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
            {(["seed", "outline"] as const).map(m => (
              <button key={m} onClick={() => setInputMode(m)}
                style={{ flex: 1, height: 30, fontSize: 12, borderRadius: 6, cursor: "pointer",
                         border: "1px solid #334155", background: inputMode === m ? "#3b82f6" : "#0f172a",
                         color: inputMode === m ? "#fff" : "#94a3b8" }}>
                {m === "seed" ? "✍️ 种子续写" : "📋 提纲复现"}
              </button>
            ))}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
            <label style={{ fontSize: 12, color: "#94a3b8" }}>章节数</label>
            <input type="number" min={1} max={200} value={chapters}
              onChange={e => setChapters(Math.max(1, Number(e.target.value) || 1))}
              style={{ width: 70, height: 28, fontSize: 13 }} />
            <span style={{ fontSize: 11, color: "#64748b" }}>= 推演轮数</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
            <label style={{ fontSize: 12, color: "#94a3b8" }}>预估总字数</label>
            <input type="number" min={0} max={5000000} step={1000} value={targetWords}
              onChange={e => setTargetWords(Math.max(0, Number(e.target.value) || 0))}
              style={{ width: 90, height: 28, fontSize: 13 }} />
            <span style={{ fontSize: 11, color: "#64748b" }}>≈ {chapters > 0 ? Math.round(targetWords / chapters) : 0} 字/章（0=不限）</span>
          </div>
          {inputMode === "outline" && (
            <div style={{ marginBottom: 6, background: "#0f172a", borderRadius: 6, padding: 8 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "#cbd5e1", marginBottom: 4 }}>角色设定</div>
              {characters.map((c, ci) => (
                <div key={ci} style={{ marginBottom: 8, borderBottom: "1px solid #1e293b", paddingBottom: 6 }}>
                  <div style={{ display: "flex", gap: 4, marginBottom: 4 }}>
                    <input placeholder="角色名" value={c.name}
                      onChange={e => setCharacters(p => p.map((x, i) => i === ci ? { ...x, name: e.target.value } : x))}
                      style={{ flex: 1, height: 26, fontSize: 12 }} />
                    <input placeholder="弧光(如 天真→果决)" value={c.arc}
                      onChange={e => setCharacters(p => p.map((x, i) => i === ci ? { ...x, arc: e.target.value } : x))}
                      style={{ flex: 1.4, height: 26, fontSize: 12 }} />
                    <button onClick={() => setCharacters(p => p.filter((_, i) => i !== ci))}
                      style={{ width: 26, height: 26, cursor: "pointer", background: "#1e293b", color: "#f87171", border: "1px solid #374151", borderRadius: 4 }}>✕</button>
                  </div>
                  {(["initial", "final"] as const).map(kind => (
                    <div key={kind} style={{ display: "flex", flexWrap: "wrap", gap: 3, marginBottom: 2 }}>
                      <span style={{ fontSize: 10, color: "#64748b", width: 30 }}>{kind === "initial" ? "初值" : "终值"}</span>
                      {LIT_METRICS.map(mt => (
                        <input key={mt} placeholder={LIT_METRIC_CN[mt]} title={LIT_METRIC_CN[mt]} value={c[kind][mt] || ""}
                          onChange={e => setCharacters(p => p.map((x, i) => i === ci ? { ...x, [kind]: { ...x[kind], [mt]: e.target.value } } : x))}
                          style={{ width: 42, height: 22, fontSize: 10 }} />
                      ))}
                    </div>
                  ))}
                </div>
              ))}
              <button onClick={() => setCharacters(p => [...p, { name: "", arc: "", initial: {}, final: {} }])}
                style={{ width: "100%", height: 26, fontSize: 12, background: "#1e293b", border: "1px solid #374151", borderRadius: 4, cursor: "pointer", color: "#cbd5e1", marginBottom: 8 }}>＋ 添加角色</button>
              <div style={{ fontSize: 12, fontWeight: 600, color: "#cbd5e1", marginBottom: 4 }}>关键事件</div>
              {keyEvents.map((ev, ei) => (
                <div key={ei} style={{ display: "flex", gap: 4, marginBottom: 4 }}>
                  <input type="number" min={1} max={chapters} value={ev.round}
                    onChange={e => setKeyEvents(p => p.map((x, i) => i === ei ? { ...x, round: Number(e.target.value) || 1 } : x))}
                    style={{ width: 46, height: 26, fontSize: 12, borderColor: (ev.round < 1 || ev.round > chapters) ? "#ef4444" : undefined }} />
                  <input placeholder="事件描述" value={ev.event}
                    onChange={e => setKeyEvents(p => p.map((x, i) => i === ei ? { ...x, event: e.target.value } : x))}
                    style={{ flex: 1, height: 26, fontSize: 12 }} />
                  <button onClick={() => setKeyEvents(p => p.filter((_, i) => i !== ei))}
                    style={{ width: 26, height: 26, cursor: "pointer", background: "#1e293b", color: "#f87171", border: "1px solid #374151", borderRadius: 4 }}>✕</button>
                </div>
              ))}
              <button onClick={() => setKeyEvents(p => [...p, { round: Math.min(chapters, p.length + 1), event: "" }])}
                style={{ width: "100%", height: 26, fontSize: 12, background: "#1e293b", border: "1px solid #374151", borderRadius: 4, cursor: "pointer", color: "#cbd5e1" }}>＋ 添加事件</button>
            </div>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept=".txt,.md,.json,.pdf,.docx,.py,.js,.ts,.rs,.go,.java,.c,.cpp,.csv,.log,.yaml,.yml"
            onChange={handleFileUpload}
            style={{ display: "none" }}
          />
          <button
            style={{ width: "100%", height: 28, fontSize: 13, marginBottom: 6, background: "#1e293b", border: "1px solid #374151", borderRadius: 6, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: 6, color: "#94a3b8" }}
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
          >
            {uploading ? "⏳" : "📎"}{" "}
            {uploading ? "解析中..." : "上传文档"}
          </button>
          <button
            className="btnPrimary"
            style={{ width: "100%", height: 32, fontSize: 13 }}
            onClick={handleCreate}
            disabled={creating}
          >
            {creating ? "创建中..." : "创建推演会话"}
          </button>
        </div>

        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>会话列表（历史推演记录）</div>
        {sessions.length === 0 && (
          <div style={{ color: "#94a3b8", fontSize: 13, textAlign: "center", padding: 20 }}>
            暂无推演会话，请创建新会话开始
          </div>
        )}
        {sessions.map(s => {
          const running = ["ontology_running", "graph_running", "agents_running", "simulating", "reporting"].includes(s.status);
          return (
            <div
              key={s.id}
              onClick={() => selectSession(s.id)}
              style={{
                padding: "8px 10px", marginBottom: 6, borderRadius: 8, cursor: "pointer",
                background: selectedId === s.id ? "#1e3a8a" : "#1e293b",
                border: "1px solid " + (selectedId === s.id ? "#3b82f6" : "#334155"),
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: "#e2e8f0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {s.title || s.id.slice(0, 8)}
                </span>
                <button
                  title={running ? "推演进行中，无法删除" : "删除记录"}
                  onClick={e => handleDelete(s.id, e)}
                  disabled={running}
                  style={{ flexShrink: 0, background: "transparent", border: "none", color: running ? "#475569" : "#f87171", cursor: running ? "not-allowed" : "pointer", fontSize: 14, lineHeight: 1, padding: "0 2px" }}
                >🗑</button>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4, alignItems: "center" }}>
                <span style={{ fontSize: 11, color: "#cbd5e1", background: "#0f172a", borderRadius: 4, padding: "1px 6px" }}>{PHASE_LABELS[s.status] || s.status}</span>
                {s.entity_count > 0 && <span style={{ fontSize: 11, color: "#94a3b8" }}>{s.entity_count} 实体</span>}
                {s.agent_count > 0 && <span style={{ fontSize: 11, color: "#94a3b8" }}>{s.agent_count} 智能体</span>}
                {s.current_round > 0 && <span style={{ fontSize: 11, color: "#94a3b8" }}>{s.current_round}/{s.total_rounds} 轮</span>}
              </div>
              <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>{(s.created_at || "").slice(0, 19).replace("T", " ")}</div>
            </div>
          );
        })}
      </div>

      {/* ── Right Panel ── */}
      <div style={{ display: "grid", gridTemplateRows: "auto auto 1fr auto", overflow: "hidden" }}>
        {selected ? (
          <>
            <div className="topbar" style={{ minHeight: 36, padding: "4px 12px" }}>
              <div className="topbarStatusRow">
                <span className="topbarWs">{selected.title || selected.id.slice(0, 8)}</span>
                <span className="pill">{PHASE_LABELS[selected.status] || selected.status}</span>
                {selected.entity_count > 0 && <span className="pill">{selected.entity_count} 实体</span>}
                {selected.relation_count > 0 && <span className="pill">{selected.relation_count} 关系</span>}
                {selected.agent_count > 0 && <span className="pill">{selected.agent_count} 智能体</span>}
                {selected.current_round > 0 && <span className="pill">{selected.current_round}/{selected.total_rounds} 轮</span>}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {selected && RUNNING_SET.has(selected.status) ? (
                  <>
                    <span style={{ fontSize: 12, color: "#22c55e", background: "#052e16", borderRadius: 4, padding: "2px 8px", marginRight: 4 }}>
                      推演中
                    </span>
                    <button className="btnSmall" style={{ marginRight: 6, background: "#ef4444", color: "#fff", border: "none" }} onClick={handleCancel} disabled={loading}>
                      停止推演
                    </button>
                  </>
                ) : selected?.status === "paused" ? (
                  <button
                    className="btnSmall"
                    style={{ marginRight: 6, background: "#16a34a", color: "#fff", border: "none" }}
                    onClick={handleStart}
                    disabled={loading}
                  >
                    继续推演
                  </button>
                ) : selected?.status === "complete" ? (
                  <button
                    className="btnSmall btnSmallPrimary"
                    style={{ marginRight: 6 }}
                    onClick={handleStart}
                    disabled={loading}
                  >
                    重新推演
                  </button>
                ) : (
                  <button
                    className="btnSmall btnSmallPrimary"
                    style={{ marginRight: 6 }}
                    onClick={handleStart}
                    disabled={loading}
                  >
                    启动推演
                  </button>
                )}
              </div>
            </div>

            {/* 主区标签切换: 图谱 / 报告 / 日志 */}
            <div style={{ display: "flex", gap: 4, padding: "6px 12px 0" }}>
              {(["graph", "report", "logs", "dashboard", "timeline", "token"] as const).map(k => (
                <button
                  key={k}
                  onClick={() => setMainTab(k)}
                  style={{
                    padding: "4px 16px", borderRadius: 6, fontSize: 13, cursor: "pointer",
                    border: "1px solid #334155",
                    background: mainTab === k ? "#3b82f6" : "#0f172a",
                    color: mainTab === k ? "#fff" : "#94a3b8",
                  }}
                >{k === "graph" ? "人物关系" : k === "report" ? "作品" : k === "logs" ? "日志" : k === "dashboard" ? "态势" : k === "timeline" ? "情节脉络" : k === "token" ? "Token" : "多结局"}</button>
              ))}
            </div>

            <div style={{ overflow: "auto", position: "relative", background: mainTab === "graph" ? "#0d1117" : "transparent" }}>
              {mainTab === "graph" && (
                graphData && graphData.nodes.length > 0 ? (
                  <div style={{ position: "absolute", top: 0, left: 0, right: 0, bottom: 0 }}>
                    <ForceGraph3D
                      ref={graphRef}
                      graphData={{
                        nodes: graphData.nodes.map(n => ({ id: n.id, name: n.name, group: n.type, desc: n.description })),
                        links: graphData.links.map(l => ({ source: l.source, target: l.target, value: l.relation })),
                      }}
                      nodeLabel={(n: any) => `${n.name}\n${n.group}`}
                      nodeColor={(n: any) => {
                        const colors: Record<string, string> = { Person: "#60a5fa", Organization: "#f59e0b", Event: "#ef4444", Concept: "#34d399", Location: "#a78bfa" };
                        return colors[n.group] || "#94a3b8";
                      }}
                      nodeVal={(n: any) => (graphData.links.filter(l => l.source === n.id || l.target === n.id).length || 1) * 2}
                      linkLabel={(l: any) => String(l.value)}
                      linkWidth={0.5}
                      backgroundColor="#0d1117"
                    />
                    <div style={{ position: "absolute", top: 8, right: 8, display: "flex", flexDirection: "column", gap: 4, zIndex: 10 }}>
                      {[
                        { label: "＋", title: "放大", onClick: () => zoomGraph(graphRef, 0.7) },
                        { label: "−", title: "缩小", onClick: () => zoomGraph(graphRef, 1.4) },
                        { label: "⊡", title: "重置视图（显示全部节点与连线）", onClick: () => resetGraph(graphRef) },
                      ].map(b => (
                        <button key={b.label} title={b.title} onClick={b.onClick}
                          style={{ width: 28, height: 28, borderRadius: 4, cursor: "pointer", background: "rgba(15,23,42,0.7)", color: "#e2e8f0", border: "1px solid #334155", fontSize: 14, lineHeight: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
                          {b.label}
                        </button>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div style={{ color: "#64748b", textAlign: "center", paddingTop: 200, fontSize: 14 }}>
                    {selected.status === "created" ? "上传文档或粘贴原文后启动推演" : "推演进行中或暂无图谱数据..."}
                  </div>
                )
              )}

              {mainTab === "report" && (
                <div style={{ padding: 16, color: "#cbd5e1", fontSize: 13, overflowY: "auto" }}>
                  {report ? (
                    <>
                      {/* ── 文学正文 + 导出 ── */}
                      {report.is_literary && report.prose && (
                        <details open style={{ marginBottom: 18 }}>
                          <summary style={{ fontSize: 14, fontWeight: 700, color: "#a78bfa", cursor: "pointer", borderLeft: "3px solid #8b5cf6", paddingLeft: 8, marginBottom: 6 }}>
                            文学正文（{report.prose.length} 字{report.style ? ` · ${report.style}` : ""}）
                            <button onClick={exportProseTxt}
                              style={{ marginLeft: 12, padding: "2px 10px", fontSize: 12, background: "#1e293b", border: "1px solid #374151", borderRadius: 6, cursor: "pointer", color: "#cbd5e1" }}>⬇ 导出 TXT</button>
                          </summary>
                          <div style={{ lineHeight: 1.9, whiteSpace: "pre-wrap", background: "#0f172a", borderRadius: 6, padding: 12 }}>{report.prose}</div>
                        </details>
                      )}
                      {/* ── 分章文件（已自动保存） ── */}
                      {report.is_literary && report.chapters && report.chapters.length > 0 && (
                        <details open style={{ marginBottom: 18 }}>
                          <summary style={{ fontSize: 14, fontWeight: 700, color: "#60a5fa", cursor: "pointer", borderLeft: "3px solid #3b82f6", paddingLeft: 8, marginBottom: 6 }}>
                            分章文件（共 {report.chapters.length} 章，已自动保存）
                          </summary>
                          {report.work_dir && (
                            <div style={{ fontSize: 12, color: "#94a3b8", marginBottom: 6, wordBreak: "break-all" }}>
                              📁 保存位置：{report.work_dir}
                            </div>
                          )}
                          <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                            {report.chapters.map(ch => (
                              <div key={ch.index} style={{ fontSize: 12, color: "#cbd5e1", background: "#0f172a", borderRadius: 4, padding: "4px 8px", display: "flex", justifyContent: "space-between" }}>
                                <span>{ch.title} · {ch.file}</span>
                                <span style={{ color: "#64748b" }}>{ch.words} 字</span>
                              </div>
                            ))}
                          </div>
                        </details>
                      )}
                      {/* ── 提纲对齐（Mode 2）── */}
                      {report.arc_alignment && report.arc_alignment.length > 0 && (
                        <details open style={{ marginBottom: 18 }}>
                          <summary style={{ fontSize: 14, fontWeight: 700, color: "#34d399", cursor: "pointer", borderLeft: "3px solid #10b981", paddingLeft: 8, marginBottom: 6 }}>
                            提纲弧光达成度
                          </summary>
                          {report.arc_alignment.map((a, i) => (
                            <div key={i} style={{ marginBottom: 6, background: "#0f172a", borderRadius: 6, padding: 8 }}>
                              <span style={{ fontWeight: 600 }}>{a.name}</span>
                              <span style={{ marginLeft: 8, color: a.win_score >= 0.9 ? "#34d399" : a.win_score >= 0.7 ? "#fbbf24" : "#f87171" }}>
                                达成 {(a.win_score * 100).toFixed(0)}%
                              </span>
                              <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 2 }}>
                                {Object.keys(a.target).map(k => `${k}: ${a.final_metrics[k] ?? "?"}/${a.target[k]}`).join("  ·  ")}
                              </div>
                            </div>
                          ))}
                        </details>
                      )}
                      {/* ── 一、推演设定 ── */}
                      {report.quantified && report.final_states ? (
                        <details open style={{ marginBottom: 18 }}>
                          <summary style={{ fontSize: 14, fontWeight: 700, color: "#60a5fa", cursor: "pointer", borderLeft: "3px solid #3b82f6", paddingLeft: 8, marginBottom: 6 }}>
                            一、量化最终状态（领域：{report.domain}）
                          </summary>
                          {Object.values(report.final_states).map((s, i) => (
                            <div key={i} style={{ marginBottom: 8, background: "#0f172a", borderRadius: 6, padding: 8, borderLeft: `3px solid ${s.alive ? "#34d399" : "#ef4444"}` }}>
                              <div style={{ fontWeight: 600 }}>
                                {s.name}{" "}
                                {s.alive
                                  ? <span style={{ fontSize: 11, color: "#34d399" }}>存活</span>
                                  : <span style={{ fontSize: 11, color: "#f87171" }}>★出局★</span>}
                              </div>
                              <div style={{ fontSize: 13, color: "#cbd5e1", marginTop: 2 }}>
                                {Object.entries(s.metrics).map(([k, v]) => `${k}=${Number(v).toFixed(0)}`).join("  ·  ")}
                              </div>
                              {s.history && s.history.length > 0 && (
                                <div style={{ fontSize: 12, color: "#64748b", marginTop: 4 }}>
                                  轨迹：{s.history.slice(-6).map((h: any, j: number) => (
                                    <span key={j} style={{ marginRight: 8 }}>[R{h.round}]{h.metric}{h.delta >= 0 ? "+" : ""}{Number(h.delta).toFixed(1)}</span>
                                  ))}
                                </div>
                              )}
                            </div>
                          ))}
                        </details>
                      ) : null}

                      {/* ── 推演总结 ── */}
                      {report.summary && (
                        <details open style={{ marginBottom: 18 }}>
                          <summary style={{ fontSize: 14, fontWeight: 700, color: "#e2e8f0", cursor: "pointer", borderLeft: "3px solid #3b82f6", paddingLeft: 8, marginBottom: 6 }}>
                            推演总结
                          </summary>
                          <div style={{ lineHeight: 1.8, whiteSpace: "pre-wrap" }}>{report.summary}</div>
                        </details>
                      )}

                      {/* ── 二、关键因果链 ── */}
                      {report.causal_summary && report.causal_summary.length > 0 && (
                        <details open style={{ marginBottom: 18 }}>
                          <summary style={{ fontSize: 14, fontWeight: 700, color: "#f59e0b", cursor: "pointer", borderLeft: "3px solid #f59e0b", paddingLeft: 8, marginBottom: 6 }}>
                            关键因果链（{report.causal_summary.length} 条）
                          </summary>
                          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                            {report.causal_summary.map((c: string, i: number) => (
                              <div key={i} style={{ background: "#0f172a", borderRadius: 6, padding: "8px 12px", borderLeft: "2px solid #f59e0b", fontSize: 12, lineHeight: 1.7 }}>
                                {c}
                              </div>
                            ))}
                          </div>
                        </details>
                      )}

                      {/* ── 三、时序因果叙事 ── */}
                      {report.stage_narratives && report.stage_narratives.length > 0 && (
                        <details open style={{ marginBottom: 18 }}>
                          <summary style={{ fontSize: 14, fontWeight: 700, color: "#a78bfa", cursor: "pointer", borderLeft: "3px solid #a78bfa", paddingLeft: 8, marginBottom: 6 }}>
                            时序因果叙事（{report.stage_narratives.length} 阶段）
                          </summary>
                          {report.stage_narratives.map((s: any, i: number) => (
                            <div key={i} style={{ marginBottom: 12, background: "#0f172a", borderRadius: 6, padding: 10, borderLeft: "2px solid #a78bfa" }}>
                              <div style={{ fontWeight: 600, color: "#c4b5fd", marginBottom: 6 }}>
                                <span style={{ background: "#312e81", padding: "1px 8px", borderRadius: 4, fontSize: 11, marginRight: 8 }}>
                                  {s.round_range || `第${(i*3+1)}-${(i+1)*3}轮`}
                                </span>
                                {s.stage || `阶段${i+1}`}
                              </div>
                              {s.start_state && <div style={{ fontSize: 12, color: "#94a3b8", marginBottom: 4, lineHeight: 1.6 }}>◉ 起始状态：{s.start_state}</div>}
                              {s.key_decisions && <div style={{ fontSize: 12, color: "#e2e8f0", marginBottom: 4, lineHeight: 1.6 }}>◉ 核心决策：{s.key_decisions}</div>}
                              {s.causal_logic && <div style={{ fontSize: 12, color: "#cbd5e1", marginBottom: 4, lineHeight: 1.6 }}>◉ 因果逻辑：{s.causal_logic}</div>}
                              {s.end_state && <div style={{ fontSize: 12, color: "#64748b", lineHeight: 1.6 }}>◉ 阶段终点：{s.end_state}</div>}
                            </div>
                          ))}
                        </details>
                      )}

                      {/* ── 四、偏离分析 ── */}
                      {report.deviation_analysis && report.deviation_analysis.length > 0 && (
                        <details open style={{ marginBottom: 18 }}>
                          <summary style={{ fontSize: 14, fontWeight: 700, color: "#fb923c", cursor: "pointer", borderLeft: "3px solid #fb923c", paddingLeft: 8, marginBottom: 6 }}>
                            决策偏离分析（{report.deviation_analysis.length} 次偏离）
                          </summary>
                          {report.deviation_analysis.map((d: any, i: number) => (
                            <div key={i} style={{ marginBottom: 8, background: "#0f172a", borderRadius: 6, padding: 10, borderLeft: "2px solid #fb923c" }}>
                              <div style={{ fontWeight: 600, color: "#fdba74", marginBottom: 4 }}>
                                第{d.round}轮 · {d.agent}
                                <span style={{ fontSize: 11, marginLeft: 8, background: d.deviation_level === "显著" ? "#7c2d12" : "#422006", padding: "1px 6px", borderRadius: 4, color: d.deviation_level === "显著" ? "#fdba74" : "#f59e0b" }}>
                                  {d.deviation_level}偏离
                                </span>
                              </div>
                              <div style={{ fontSize: 12, color: "#e2e8f0", marginBottom: 2 }}>决策：{d.decision}</div>
                              <div style={{ fontSize: 12, color: "#94a3b8" }}>原因：{d.reason}</div>
                            </div>
                          ))}
                        </details>
                      )}

                      {/* ── 五、结论与建议 ── */}
                      {(report.conclusion || (report.risk_alerts && report.risk_alerts.length > 0) || (report.recommendations && report.recommendations.length > 0)) && (
                        <details open style={{ marginBottom: 18 }}>
                          <summary style={{ fontSize: 14, fontWeight: 700, color: "#e2e8f0", cursor: "pointer", borderLeft: "3px solid #60a5fa", paddingLeft: 8, marginBottom: 6 }}>
                            结论与建议
                          </summary>
                          {report.conclusion && (
                            <div style={{ marginBottom: 12, lineHeight: 1.8, whiteSpace: "pre-wrap" }}>
                              {report.conclusion}
                            </div>
                          )}
                          {report.risk_alerts && report.risk_alerts.length > 0 && (
                            <div style={{ marginBottom: 12 }}>
                              <div style={{ fontSize: 12, fontWeight: 600, color: "#f87171", marginBottom: 4 }}>风险预警</div>
                              <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
                                {report.risk_alerts.map((x, i) => <li key={i}>{x}</li>)}
                              </ul>
                            </div>
                          )}
                          {report.recommendations && report.recommendations.length > 0 && (
                            <div>
                              <div style={{ fontSize: 12, fontWeight: 600, color: "#34d399", marginBottom: 4 }}>创作建议</div>
                              <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
                                {report.recommendations.map((x, i) => <li key={i}>{x}</li>)}
                              </ul>
                            </div>
                          )}
                        </details>
                      )}

                      {/* ── 附录：关键事件 ── */}
                      {report.key_events && report.key_events.length > 0 && (
                        <details style={{ marginBottom: 18 }}>
                          <summary style={{ fontSize: 14, fontWeight: 700, color: "#64748b", cursor: "pointer", borderLeft: "3px solid #475569", paddingLeft: 8, marginBottom: 6 }}>
                            附录 · 关键事件 ({report.key_events.length} 条)
                          </summary>
                          <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
                            {report.key_events.map((ev, i) => {
                              const text = typeof ev === "string" ? ev : (ev?.description || JSON.stringify(ev));
                              const round = ev && typeof ev === "object" && ev.round ? `[第${ev.round}轮] ` : "";
                              const sig = ev && typeof ev === "object" && ev.significance ? `（${ev.significance}）` : "";
                              return <li key={i}>{round}{text}{sig}</li>;
                            })}
                          </ul>
                        </details>
                      )}
                    </>
                  ) : (
                    <div style={{ color: "#64748b", textAlign: "center", paddingTop: 60 }}>
                      {selected.status === "complete" ? "暂无报告数据" : "推演完成后将生成报告"}
                    </div>
                  )}
                </div>
              )}

              {mainTab === "dashboard" && (
                <div style={{ padding: 16, color: "#cbd5e1", fontSize: 13, overflowY: "auto" }}>
                  {snapshot ? (
                    <>
                      {/* ── 情节推进 ── */}
                      <div style={{ marginBottom: 14, background: "#0f172a", borderRadius: 8, padding: 12, borderLeft: "3px solid #a78bfa" }}>
                        <div style={{ fontSize: 14, fontWeight: 700, color: "#a78bfa", marginBottom: 8 }}>
                          📖 第 {snapshot.round || "?"} 轮 · 情节推进
                        </div>
                        {snapshot.recent_structured && snapshot.recent_structured.length > 0 ? (
                          snapshot.recent_structured.map((r: any, i: number) => (
                            <div key={i} style={{ fontSize: 12, color: "#94a3b8", marginBottom: 3, paddingLeft: 8, borderLeft: "2px solid #334155" }}>
                              <span style={{ color: "#cbd5e1", fontWeight: 500 }}>{r.agent_name || "?"}</span>
                              <span style={{ color: "#64748b", margin: "0 4px" }}>→</span>
                              {r.action}
                              {r.content && <span style={{ color: "#64748b", marginLeft: 4 }}>"{(r.content || "").slice(0, 80)}{(r.content || "").length > 80 ? "..." : ""}"</span>}
                            </div>
                          ))
                        ) : (
                          <div style={{ fontSize: 12, color: "#64748b" }}>等待首轮事件...</div>
                        )}
                      </div>

                      {/* ── 角色弧光追踪 ── */}
                      <div style={{ marginBottom: 14 }}>
                        <div style={{ fontSize: 14, fontWeight: 700, color: "#e2e8f0", marginBottom: 10, borderLeft: "3px solid #22c55e", paddingLeft: 8 }}>🎭 角色弧光追踪</div>
                        {snapshot.entities && snapshot.entities.length > 0 ? (
                          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                            {snapshot.entities.map((e: any) => {
                              const metrics = e.metrics || {};
                              const keys = Object.keys(metrics).slice(0, 4);
                              return (
                                <div key={e.name} style={{ background: "#0f172a", borderRadius: 6, padding: "8px 12px" }}>
                                  <div style={{ fontSize: 13, fontWeight: 600, color: "#e2e8f0", marginBottom: 4 }}>{e.name}</div>
                                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                                    {keys.map((k: string) => {
                                      const val = metrics[k] || 0;
                                      const hue = k === "trust" || k === "affection" ? 200 : k === "tension" ? 0 : k === "power" ? 120 : 40;
                                      const pct = Math.min(100, val);
                                      return (
                                        <div key={k} style={{ flex: "1 1 45%", minWidth: 120 }}>
                                          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 2 }}>
                                            <span style={{ color: "#94a3b8" }}>{LIT_METRIC_CN[k] || k}</span>
                                            <span style={{ color: "#cbd5e1", fontWeight: 600 }}>{val}</span>
                                          </div>
                                          <div style={{ height: 4, background: "#1e293b", borderRadius: 2, overflow: "hidden" }}>
                                            <div style={{ height: "100%", width: `${pct}%`, background: `hsl(${hue},60%,50%)`, borderRadius: 2, transition: "width 0.5s" }} />
                                          </div>
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        ) : (
                          <div style={{ fontSize: 12, color: "#64748b" }}>等待角色数据...</div>
                        )}
                      </div>

                      {/* ── 故事节奏 ── */}
                      {snapshot.averages && Object.keys(snapshot.averages).length > 0 && (
                        <div style={{ marginBottom: 14, background: "#0f172a", borderRadius: 8, padding: 12, borderLeft: "3px solid #f59e0b" }}>
                          <div style={{ fontSize: 14, fontWeight: 700, color: "#f59e0b", marginBottom: 8 }}>⏱ 故事节奏</div>
                          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, fontSize: 12 }}>
                            {Object.entries(snapshot.averages).map(([k, v]: [string, any]) => (
                              <span key={k} style={{ background: "#1e293b", borderRadius: 4, padding: "2px 8px", color: "#cbd5e1" }}>
                                {LIT_METRIC_CN[k] || k}：{typeof v === 'number' ? v.toFixed(0) : v}
                              </span>
                            ))}
                          </div>
                          <div style={{ fontSize: 11, color: "#64748b", marginTop: 6 }}>
                            全局均值 · 共 {snapshot.entity_count || 0} 个角色
                          </div>
                        </div>
                      )}

                      {/* ── 已生成章节 ── */}
                      {report?.chapters && report.chapters.length > 0 && (
                        <div style={{ marginBottom: 14, background: "#0f172a", borderRadius: 8, padding: 12, borderLeft: "3px solid #3b82f6" }}>
                          <div style={{ fontSize: 14, fontWeight: 700, color: "#60a5fa", marginBottom: 8 }}>📄 已生成章节</div>
                          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, fontSize: 12 }}>
                            {report.chapters.map((ch: any) => (
                              <span key={ch.index} style={{ background: "#1e293b", borderRadius: 4, padding: "2px 8px", color: "#cbd5e1" }}>
                                Ch{ch.index} {ch.words}字
                              </span>
                            ))}
                          </div>
                          {report.work_dir && (
                            <div style={{ fontSize: 11, color: "#64748b", marginTop: 6 }}>
                              📁 {report.work_dir}
                            </div>
                          )}
                        </div>
                      )}
                    </>
                  ) : (
                    <div style={{ color: "#64748b", textAlign: "center", paddingTop: 60 }}>
                      {selected?.status === "complete" ? "✅ 创作已完成，请查看「作品」Tab"
                        : selected?.status ? (["simulating", "reporting"].includes(selected.status) ? "⏳ 等待首轮创作数据..."
                        : "创作启动后将显示实时态势")
                        : "📌 请创建会话并启动创作"}
                    </div>
                  )}
                </div>
              )}

              {mainTab === "timeline" && (
                <div style={{ padding: 16, color: "#cbd5e1", fontSize: 13, display: "flex", flexDirection: "column", height: "100%" }}>
                  <div style={{ display: "flex", gap: 6, marginBottom: 12 }}>
                    {(["timeline", "causal"] as const).map(v => (
                      <button key={v} onClick={() => setTimelineView(v)} style={{ padding: "3px 12px", borderRadius: 6, fontSize: 13, cursor: "pointer", border: "1px solid #334155", background: timelineView === v ? "#3b82f6" : "#0f172a", color: timelineView === v ? "#fff" : "#94a3b8" }}>{v === "timeline" ? "情节时间线" : "人物影响链"}</button>
                    ))}
                  </div>
                  <div style={{ flex: 1, overflow: "auto" }}>
                  {timelineView === "timeline" ? (
                  timeline && (timeline.timelines.length > 0 || timeline.sequence.length > 0) ? (
                    <>
                      <div style={{ marginBottom: 18 }}>
                        <div style={{ fontSize: 13, fontWeight: 700, color: "#60a5fa", marginBottom: 6, borderLeft: "3px solid #3b82f6", paddingLeft: 8 }}>
                          智能体行动时间线
                        </div>
                        {timeline.timelines.map((t, i) => {
                          const hasFsm = t.actions.some(a => a.driver === "fsm");
                          const canOverride = selected?.status === "simulating";
                          return (
                          <div key={i} style={{ marginBottom: 10, background: "#0f172a", borderRadius: 6, padding: 8 }}>
                            <div style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}>
                              <span>{t.agent_name}</span>
                              {hasFsm && canOverride && (
                                <button onClick={() => { setOvAgent(ovAgent === t.agent_name ? null : t.agent_name); setOvAction(""); }}
                                  title="FSM 接管中，可强制指定该角色下一步动作"
                                  style={{ fontSize: 11, padding: "1px 8px", borderRadius: 6, cursor: "pointer", border: "1px solid #374151", background: "#1e293b", color: "#fbbf24" }}>⚙ 干预</button>
                              )}
                            </div>
                            {ovAgent === t.agent_name && (
                              <div style={{ display: "flex", flexWrap: "wrap", gap: 4, margin: "6px 0", background: "#1e293b", borderRadius: 6, padding: 6 }}>
                                <input value={ovAction} onChange={e => setOvAction(e.target.value)} placeholder="强制动作(如 confront)" style={{ flex: 1, minWidth: 100, height: 26, fontSize: 12 }} />
                                <input value={ovTarget} onChange={e => setOvTarget(e.target.value)} placeholder="目标(可选)" list="ov-ents" style={{ width: 90, height: 26, fontSize: 12 }} />
                                <input type="number" min={0} max={1} step={0.1} value={ovIntensity} onChange={e => setOvIntensity(Number(e.target.value))} title="强度" style={{ width: 56, height: 26, fontSize: 12 }} />
                                <input type="number" min={1} max={20} value={ovRounds} onChange={e => setOvRounds(Math.max(1, Number(e.target.value) || 1))} title="轮数" style={{ width: 48, height: 26, fontSize: 12 }} />
                                <button onClick={() => submitFsmOverride(t.agent_name)} style={{ height: 26, fontSize: 12, padding: "0 10px", borderRadius: 6, cursor: "pointer", border: "none", background: "#3b82f6", color: "#fff" }}>强制</button>
                                <datalist id="ov-ents">{(graphData?.nodes || []).map(n => <option key={n.id} value={n.name} />)}</datalist>
                              </div>
                            )}
                            <ul style={{ margin: "4px 0 0", paddingLeft: 18, lineHeight: 1.7 }}>
                              {t.actions.map((a, j) => (
                                <li key={j}>
                                  <span style={{ color: "#a78bfa" }}>{a.action}</span>
                                  {a.driver === "fsm" ? <span style={{ color: "#64748b", fontSize: 11 }}> [FSM]</span>
                                    : a.driver === "forced" ? <span style={{ color: "#fbbf24", fontSize: 11 }}> [强制]</span> : null}
                                  {a.description ? <span> — {a.description}</span> : null}
                                  {a.effect ? <span style={{ marginLeft: 6, fontSize: 11, color: a.effect.includes("-") ? "#f87171" : "#34d399" }}>（{a.effect}）</span> : null}
                                </li>
                              ))}
                            </ul>
                          </div>
                          );
                        })}
                      </div>
                      {timeline.sequence.length > 0 && (
                        <div>
                          <div style={{ fontSize: 13, fontWeight: 700, color: "#e2e8f0", marginBottom: 6, borderLeft: "3px solid #a78bfa", paddingLeft: 8 }}>事件序列（按时间）</div>
                          <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
                            {timeline.sequence.map((e, i) => (
                              <li key={i}><span style={{ color: "#94a3b8" }}>{e.agent_name}</span> {e.action}{e.description ? `: ${e.description}` : ""}
                                {e.effect ? <span style={{ marginLeft: 6, fontSize: 11, color: e.effect.includes("-") ? "#f87171" : "#34d399" }}>（{e.effect}）</span> : null}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </>
                  ) : (
                    <div style={{ color: "#64748b", textAlign: "center", paddingTop: 60 }}>
                      {selected.status === "complete" ? "暂无行动时序数据" : "推演完成后将生成行动时间线"}
                    </div>
                  )
                  ) : (
                     causal && causal.nodes.length > 0 ? (
                       <>
                         <div style={{ flex: 1, minHeight: 250, marginBottom: 12, background: "#0d1117", borderRadius: 6, position: "relative" }}>
                           <div style={{ position: "absolute", top: 0, left: 0, right: 0, bottom: 0 }}>
                             <ForceGraph3D
                             ref={causalGraphRef}
                             graphData={{
                               nodes: causal.nodes.map(n => ({ id: n.id, name: n.label, group: n.kind })),
                               links: causal.links.map(l => ({ source: l.source, target: l.target, value: l.label })),
                             }}
                             nodeLabel={(n: any) => `${n.name}\n${n.group}`}
                             nodeColor={(n: any) => n.group === "agent" ? "#3b82f6" : n.group === "event" ? "#a78bfa" : "#f59e0b"}
                             linkLabel={(l: any) => String(l.value)}
                             linkDirectionalArrowLength={3}
                             backgroundColor="#0d1117"
                             onNodeClick={(n: any) => setSelectedCausalNode(n ? n.id : null)}
                             />
                           </div>
                           <div style={{ position: "absolute", top: 8, right: 8, display: "flex", flexDirection: "column", gap: 4, zIndex: 10 }}>
                             {[
                               { label: "＋", title: "放大", onClick: () => zoomGraph(causalGraphRef, 0.7) },
                               { label: "−", title: "缩小", onClick: () => zoomGraph(causalGraphRef, 1.4) },
                               { label: "⊡", title: "重置视图（显示全部节点与连线）", onClick: () => resetGraph(causalGraphRef) },
                             ].map(b => (
                               <button key={b.label} title={b.title} onClick={b.onClick}
                                 style={{ width: 28, height: 28, borderRadius: 4, cursor: "pointer", background: "rgba(15,23,42,0.7)", color: "#e2e8f0", border: "1px solid #334155", fontSize: 14, lineHeight: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
                                 {b.label}
                               </button>
                             ))}
                           </div>
                         </div>
                         {selectedCausalNode && (
                           <div style={{ marginBottom: 12, background: "#0f172a", borderRadius: 6, padding: 10, borderLeft: "3px solid #60a5fa" }}>
                             <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                               <span style={{ fontWeight: 600, color: "#60a5fa", fontSize: 12 }}>
                                 📌 选中：{causal.nodes.find(n => n.id === selectedCausalNode)?.label || selectedCausalNode}
                               </span>
                               <button onClick={() => setSelectedCausalNode(null)} style={{ background: "none", border: "none", color: "#94a3b8", cursor: "pointer", fontSize: 14 }}>✕</button>
                             </div>
                              <div style={{ fontSize: 12, color: "#94a3b8", lineHeight: 1.6 }}>
                                {(() => {
                                  const node = causal.nodes.find(n => n.id === selectedCausalNode);
                                  if (!node) return null;
                                  const descParts: any[] = [];
                                  if (node.kind) descParts.push(<div key="kind">类型：{node.kind}</div>);
                                  if (node.desc) descParts.push(<div key="desc">{node.desc}</div>);
                                  // Collect connected nodes by direction
                                  const relatedLinks = causal.links.filter(l => l.source === selectedCausalNode || l.target === selectedCausalNode);
                                  if (relatedLinks.length === 0) return descParts;
                                  const incomingLinks = relatedLinks.filter(l => l.target === selectedCausalNode);
                                  const outgoingLinks = relatedLinks.filter(l => l.source === selectedCausalNode);
                                  // Helper: get top-5 connected nodes sorted by link count
                                  const getTopNodes = (links: typeof relatedLinks, pickId: (l: typeof relatedLinks[0]) => string, limit: number) => {
                                    const nodeIds = new Set(links.map(pickId));
                                    const scored = Array.from(nodeIds).map(id => {
                                      const n = causal.nodes.find(nn => nn.id === id);
                                      const count = links.filter(l => pickId(l) === id).length;
                                      return { node: n, count };
                                    }).filter(x => x.node).sort((a, b) => b.count - a.count).slice(0, limit);
                                    return scored;
                                  };
                                  // Incoming nodes (source → selected)
                                  if (incomingLinks.length > 0) {
                                    const topIn = getTopNodes(incomingLinks, l => l.source, 5);
                                    descParts.push(<div key="in-title" style={{ marginTop: 6, color: "#a78bfa", fontWeight: 600 }}>──── 来源（{topIn.length} 个节点指向此处）</div>);
                                    topIn.forEach((x, i) => {
                                      const lbl = x.node!.label + (x.node!.desc ? ` — ${x.node!.desc.slice(0, 80)}` : "");
                                      descParts.push(<div key={`in-${i}`} style={{ paddingLeft: 8 }}>• [{x.node!.kind}] {lbl}</div>);
                                    });
                                  }
                                  // Outgoing nodes (selected → target)
                                  if (outgoingLinks.length > 0) {
                                    const topOut = getTopNodes(outgoingLinks, l => l.target, 5);
                                    descParts.push(<div key="out-title" style={{ marginTop: 6, color: "#60a5fa", fontWeight: 600 }}>──── 去向（指向 {topOut.length} 个节点）</div>);
                                    topOut.forEach((x, i) => {
                                      const lbl = x.node!.label + (x.node!.desc ? ` — ${x.node!.desc.slice(0, 80)}` : "");
                                      descParts.push(<div key={`out-${i}`} style={{ paddingLeft: 8 }}>• [{x.node!.kind}] {lbl}</div>);
                                    });
                                  }
                                  return descParts;
                                })()}
                              </div>
                           </div>
                         )}
                         <div>
                           <div style={{ fontSize: 13, fontWeight: 700, color: "#f87171", marginBottom: 6, borderLeft: "3px solid #ef4444", paddingLeft: 8 }}>因果归因（源 → 目标 累计指标影响，负=致衰）</div>
                           <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
                             {causal.summary.map((s, i) => (
                               <li key={i}><span style={{ color: "#94a3b8" }}>{s.source}</span> → <span style={{ color: "#94a3b8" }}>{s.target}</span>: {s.metric}{s.amount >= 0 ? "+" : ""}{s.amount}</li>
                             ))}
                           </ul>
                         </div>
                       </>
                    ) : (
                      <div style={{ color: "#64748b", textAlign: "center", paddingTop: 60 }}>
                        {selected.status === "complete" ? "暂无因果数据" : "推演完成后将生成因果图"}
                      </div>
                    )
                  )}
                </div>
                </div>
              )}

              {mainTab === "logs" && (
                <div ref={logsRef} style={{ padding: 8, fontSize: 12 }}>
                  {logs.length === 0 && (
                    <div style={{ color: "#94a3b8", textAlign: "center", padding: 10 }}>暂无日志</div>
                  )}
                  {logs.map((l, i) => (
                    <div key={i} style={{ padding: "1px 0", color: "#94a3b8", fontFamily: "monospace" }}>
                      <span style={{ color: "#3b82f6", marginRight: 8 }}>[{l.phase}]</span>
                      {l.message}
                    </div>
                  ))}
                </div>
              )}

              {mainTab === "token" && (
                <div style={{ padding: 16, color: "#cbd5e1", fontSize: 13, overflowY: "auto" }}>
                  {tokenData ? (
                    <>
                      {/* 总览卡片 */}
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 10, marginBottom: 20 }}>
                        {(() => {
                          const fmt = (n: number) => n < 10000 ? `${n}` : n < 1_000_000 ? `${(n / 1000).toFixed(1)}K` : `${(n / 1_000_000).toFixed(2)}M`;
                          const t = (n: number) => `${fmt(n)} (入${fmt(tokenData.total_prompt_tokens)}/出${fmt(tokenData.total_completion_tokens)})`;
                          return [
                          { label: "总 Tokens", value: fmt(tokenData.total_tokens), color: "#3b82f6" },
                          { label: "输入", value: fmt(tokenData.total_prompt_tokens), color: "#8b5cf6" },
                          { label: "输出", value: fmt(tokenData.total_completion_tokens), color: "#06b6d4" },
                          { label: "输入/输出比", value: tokenData.total_prompt_tokens > 0 ? `1:${(tokenData.total_completion_tokens / tokenData.total_prompt_tokens).toFixed(2)}` : "N/A", color: "#f59e0b" },
                        ].map(c => (
                          <div key={c.label} style={{ background: "#0f172a", borderRadius: 8, padding: 12, borderLeft: `3px solid ${c.color}` }}>
                            <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 4 }}>{c.label}</div>
                            <div style={{ fontSize: 18, fontWeight: 700, color: c.color }}>{c.value}</div>
                          </div>
                        ))})()}
                      </div>

                      {/* 各阶段分布 */}
                      {tokenData.phases && Object.keys(tokenData.phases).length > 0 && (
                        <div style={{ marginBottom: 20 }}>
                          <div style={{ fontSize: 14, fontWeight: 700, color: "#e2e8f0", marginBottom: 10, borderLeft: "3px solid #3b82f6", paddingLeft: 8 }}>各阶段分布</div>
                          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                            {Object.entries(tokenData.phases).map(([phase, pdata]) => (
                              <div key={phase} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                <span style={{ width: 80, fontSize: 12, color: "#94a3b8", textAlign: "right", flexShrink: 0 }}>{phase === "ontology" ? "本体生成" : phase === "quantify" ? "量化识别" : phase === "graph" ? "图谱构建" : phase === "agents" ? "智能体工厂" : phase === "simulation" ? "模拟推演" : phase === "report" ? "报告生成" : phase === "resume" ? "续推恢复" : phase}</span>
                                <div style={{ flex: 1, height: 20, background: "#0f172a", borderRadius: 4, overflow: "hidden", display: "flex" }}>
                                   {tokenData.total_tokens > 0 && (
                                    <>
                                      <div style={{ height: "100%", background: "#8b5cf6", width: `${(pdata.prompt / tokenData.total_tokens * 100).toFixed(1)}%` }} title={`输入 ${pdata.prompt.toLocaleString()}`} />
                                      <div style={{ height: "100%", background: "#06b6d4", width: `${(pdata.completion / tokenData.total_tokens * 100).toFixed(1)}%` }} title={`输出 ${pdata.completion.toLocaleString()}`} />
                                    </>
                                  )}
                                </div>
                                <span style={{ width: 60, fontSize: 11, color: "#64748b", textAlign: "left", flexShrink: 0 }}>{pdata.total < 10000 ? pdata.total : pdata.total < 1_000_000 ? (pdata.total/1000).toFixed(1) + "K" : (pdata.total/1_000_000).toFixed(2) + "M"}</span>
                              </div>
                            ))}
                          </div>
                          <div style={{ display: "flex", gap: 16, marginTop: 8, fontSize: 11, color: "#64748b" }}>
                            <span><span style={{ color: "#8b5cf6" }}>■</span> 输入</span>
                            <span><span style={{ color: "#06b6d4" }}>■</span> 输出</span>
                          </div>
                        </div>
                      )}

                      {/* 轮次柱状图 */}
                      {tokenData.rounds && Object.keys(tokenData.rounds).length > 0 && (
                        <div style={{ marginBottom: 20 }}>
                          <div style={{ fontSize: 14, fontWeight: 700, color: "#e2e8f0", marginBottom: 10, borderLeft: "3px solid #f59e0b", paddingLeft: 8 }}>Token 消耗趋势 (每轮)</div>
                          {(() => {
                            const rounds = Object.entries(tokenData.rounds);
                            if (rounds.length === 0) return null;
                            const maxTotal = Math.max(...rounds.map(([, r]) => r.total), 1);
                            const barW = Math.max(12, Math.min(40, 800 / rounds.length));
                            const svgH = 160; const svgW = rounds.length * (barW + 6) + 40;
                            const chartH = 120; const padL = 50; const padB = 30;
                            return (
                              <div style={{ overflowX: "auto" }}>
                                <svg width={svgW} height={svgH} style={{ display: "block" }}>
                                  {/* Y axis labels */}
                                  {[0, 1, 2, 3, 4].map(i => {
                                    const y = chartH - (i / 4) * chartH + 10;
                                    const val = maxTotal < 10000 ? (maxTotal * i / 4).toFixed(0)
                                      : maxTotal < 1_000_000 ? ((maxTotal / 1000) * i / 4).toFixed(1) + "K"
                                      : ((maxTotal / 1_000_000) * i / 4).toFixed(1) + "M";
                                    return <text key={i} x={padL - 8} y={y + 4} textAnchor="end" fill="#64748b" fontSize={10}>{val}</text>;
                                  })}
                                  {/* Grid lines */}
                                  {[0, 1, 2, 3, 4].map(i => {
                                    const y = chartH - (i / 4) * chartH + 10;
                                    return <line key={i} x1={padL} y1={y} x2={svgW} y2={y} stroke="#1e293b" strokeWidth={1} />;
                                  })}
                                  {rounds.map(([rnd, rdata], idx) => {
                                    const x = padL + idx * (barW + 6);
                                    const hPrompt = maxTotal > 0 ? (rdata.prompt / maxTotal) * chartH : 0;
                                    const hCompl = maxTotal > 0 ? (rdata.completion / maxTotal) * chartH : 0;
                                    const yBase = chartH + 10;
                                    return (
                                      <g key={rnd}>
                                        <rect x={x} y={yBase - hPrompt - hCompl} width={barW} height={hPrompt + hCompl} fill="#1e293b" rx={2} />
                                        <rect x={x} y={yBase - hPrompt - hCompl} width={barW} height={hPrompt} fill="#8b5cf6" rx={2} />
                                        <rect x={x} y={yBase - hCompl} width={barW} height={hCompl} fill="#06b6d4" rx={2} />
                                        <text x={x + barW / 2} y={yBase + 14} textAnchor="middle" fill="#64748b" fontSize={9}>R{rnd}</text>
                                        <title>{`R${rnd}: 入${rdata.prompt.toLocaleString()} 出${rdata.completion.toLocaleString()} 合计${rdata.total.toLocaleString()}`}</title>
                                      </g>
                                    );
                                  })}
                                </svg>
                              </div>
                            );
                          })()}
                        </div>
                      )}
                    </>
                  ) : (
                    <div style={{ color: "#64748b", textAlign: "center", paddingTop: 60 }}>
                      {selected?.status && RUNNING_SET.has(selected.status) ? "等待 LLM 调用统计..." : "暂无 Token 统计数据"}
                    </div>
                  )}
                </div>
              )}
            </div>

            {selected.status === "simulating" && (
              <div style={{ display: "flex", gap: 6, padding: "6px 12px", borderTop: "1px solid #374151", background: "#1e293b" }}>
                <input
                  style={{ flex: 1, height: 28, fontSize: 13, width: "100%" }}
                  placeholder="输入剧情走向指令（例如：让主角在下一章坦白身世）"
                  value={interventionText}
                  onChange={e => setInterventionText(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") sendIntervention(); }}
                />
                <button
                  className="btnSmall btnSmallPrimary"
                  style={{ height: 28, fontSize: 12 }}
                  onClick={sendIntervention}
                  disabled={sending || !interventionText.trim()}
                >
                  {sending ? "发送中..." : "发送干预"}
                </button>
              </div>
            )}
          </>
        ) : (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "#94a3b8", fontSize: 14 }}>
            请选择一个推演会话以开始
          </div>
        )}
      </div>

      {/* ── Settings Overlay ── */}
      {showSettings && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center" }} onClick={() => setShowSettings(false)}>
          <div style={{ background: "#1e293b", borderRadius: 12, padding: 24, width: 520, maxHeight: "80vh", overflow: "auto", border: "1px solid #334155" }} onClick={e => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <h2 style={{ margin: 0, fontSize: 18 }}>LLM / 嵌入模型配置</h2>
              <button onClick={() => setShowSettings(false)} style={{ background: "none", border: "none", color: "#94a3b8", cursor: "pointer", fontSize: 20 }}>✕</button>
            </div>

            {/* Tabs */}
            <div style={{ display: "flex", gap: 4, marginBottom: 16 }}>
              <button onClick={() => setSettingsTab("llm")} style={{ flex: 1, padding: "6px 0", borderRadius: 6, border: "1px solid #334155", background: settingsTab === "llm" ? "#3b82f6" : "#0f172a", color: settingsTab === "llm" ? "#fff" : "#94a3b8", cursor: "pointer", fontSize: 13 }}>LLM 对话模型</button>
              <button onClick={() => setSettingsTab("embed")} style={{ flex: 1, padding: "6px 0", borderRadius: 6, border: "1px solid #334155", background: settingsTab === "embed" ? "#3b82f6" : "#0f172a", color: settingsTab === "embed" ? "#fff" : "#94a3b8", cursor: "pointer", fontSize: 13 }}>嵌入模型</button>
              <button onClick={() => setSettingsTab("engine")} style={{ flex: 1, padding: "6px 0", borderRadius: 6, border: "1px solid #334155", background: settingsTab === "engine" ? "#3b82f6" : "#0f172a", color: settingsTab === "engine" ? "#fff" : "#94a3b8", cursor: "pointer", fontSize: 13 }}>引擎</button>
            </div>

            {settingsTab === "llm" ? (
              <>
                <label style={lbl}>服务商</label>
                {cfgProviders.length === 0 ? (
                  <div style={{ color: "#f59e0b", fontSize: 13, marginBottom: 8 }}>⚠ 无法加载服务商列表 — 请确认后端已启动 (http://127.0.0.1:8760/health)</div>
                ) : (
                <select value={cfgLLMProvider} onChange={e => { setCfgLLMProvider(e.target.value); const p = cfgProviders.find(x => x.slug === e.target.value); if (p?.default_llm_base_url) { setCfgLLMBase(p.default_llm_base_url); setCfgLLMTest(""); } }} style={{ ...inp, background: "#1e293b", color: "#e2e8f0", border: "1px solid #334155", borderRadius: 6 }}>
                  <option value="">选择服务商...</option>
                  {cfgProviders.map(p => <option key={p.slug} value={p.slug}>{p.name}{p.note ? ` (${p.note})` : ""}</option>)}
                </select>
                )}

                <label style={lbl}>API 地址</label>
                <input style={inp} value={cfgLLMBase} onChange={e => setCfgLLMBase(e.target.value)} placeholder="http://127.0.0.1:1234/v1" />
                <div style={{ marginTop: 4, marginBottom: 12, display: "flex", gap: 8 }}>
                  <button onClick={testLLM} disabled={cfgLLMTest === "testing"} style={{ ...btn, background: "#334155", color: "#e2e8f0" }}>
                    {cfgLLMTest === "testing" ? "测试中..." : cfgLLMTest === "ok" ? "✓ 连接成功" : cfgLLMTest === "fail" ? "✗ 连接失败" : "测试连接"}
                  </button>
                </div>

                <label style={lbl}>API Key</label>
                <input style={inp} type="password" value={cfgLLMKey} onChange={e => setCfgLLMKey(e.target.value)} placeholder="sk-... (LM Studio 无需填写)" />

                <label style={lbl}>模型名称</label>
                <input style={inp} value={cfgLLMModel} onChange={e => setCfgLLMModel(e.target.value)} placeholder="qwen/qwen3.5-9b" />
                <div style={{ marginTop: 4, marginBottom: 12 }}>
                  <button onClick={fetchModels} disabled={cfgFetchingModels} style={{ ...btn, background: "#334155", color: "#e2e8f0" }}>{cfgFetchingModels ? "获取中..." : "拉取模型列表"}</button>
                </div>
              </>
            ) : settingsTab === "embed" ? (
              <>
                <label style={lbl}>服务商</label>
                <select value={cfgEmbedProvider} onChange={e => { setCfgEmbedProvider(e.target.value); const p = cfgProviders.find(x => x.slug === e.target.value); if (p?.default_llm_base_url) { setCfgEmbedBase(p.default_llm_base_url); } }} style={{ ...inp, background: "#1e293b", color: "#e2e8f0", border: "1px solid #334155", borderRadius: 6 }}>
                  <option value="">独立配置...</option>
                  {cfgProviders.map(p => <option key={p.slug} value={p.slug}>{p.name}{p.default_embed_model ? ` (${p.default_embed_model})` : ""}{p.note ? ` — ${p.note}` : ""}</option>)}
                </select>

                <label style={lbl}>嵌入 API 地址</label>
                <input style={inp} value={cfgEmbedBase} onChange={e => setCfgEmbedBase(e.target.value)} placeholder="http://127.0.0.1:1234/v1" />

                <label style={lbl}>嵌入 API Key</label>
                <input style={inp} type="password" value={cfgEmbedKey} onChange={e => setCfgEmbedKey(e.target.value)} placeholder="嵌入服务 API Key" />

                <label style={lbl}>嵌入模型名称</label>
                <input style={inp} value={cfgEmbedModel} onChange={e => setCfgEmbedModel(e.target.value)} placeholder="text-embedding-3-small" />
                <div style={{ marginTop: 4, marginBottom: 12 }}>
                  <button onClick={fetchModels} disabled={cfgFetchingModels} style={{ ...btn, background: "#334155", color: "#e2e8f0" }}>{cfgFetchingModels ? "获取中..." : "拉取模型列表"}</button>
                </div>
              </>
            ) : null}

            {/* LLM Model list */}
            {settingsTab === "llm" && cfgLLMFetchedModels.length > 0 && (
              <div style={{ maxHeight: 180, overflow: "auto", marginBottom: 16, background: "#0f172a", borderRadius: 6, padding: 8 }}>
                <div style={{ fontSize: 12, color: "#64748b", marginBottom: 4 }}>对话模型 ({cfgLLMFetchedModels.length})</div>
                {cfgLLMFetchedModels.map(m => (
                  <div key={m} onClick={() => setCfgLLMModel(m)} style={{ padding: "3px 6px", cursor: "pointer", borderRadius: 4, fontSize: 13, color: cfgLLMModel === m ? "#3b82f6" : "#cbd5e1" }}>{m}</div>
                ))}
              </div>
            )}
            {settingsTab === "llm" && cfgLLMModelError && <div style={{ color: "#ef4444", fontSize: 13, marginBottom: 12 }}>{cfgLLMModelError}</div>}
            {/* Embed Model list */}
            {settingsTab === "embed" && cfgEmbedFetchedModels.length > 0 && (
              <div style={{ maxHeight: 180, overflow: "auto", marginBottom: 16, background: "#0f172a", borderRadius: 6, padding: 8 }}>
                <div style={{ fontSize: 12, color: "#64748b", marginBottom: 4 }}>嵌入模型 ({cfgEmbedFetchedModels.length})</div>
                {cfgEmbedFetchedModels.map(m => (
                  <div key={m} onClick={() => setCfgEmbedModel(m)} style={{ padding: "3px 6px", cursor: "pointer", borderRadius: 4, fontSize: 13, color: cfgEmbedModel === m ? "#3b82f6" : "#cbd5e1" }}>{m}</div>
                ))}
              </div>
            )}
            {settingsTab === "embed" && cfgEmbedModelError && <div style={{ color: "#ef4444", fontSize: 13, marginBottom: 12 }}>{cfgEmbedModelError}</div>}

            {/* ── 引擎配置 Tab ── */}
            {settingsTab === "engine" && (
              <>
                <div style={{ fontSize: 13, fontWeight: 700, color: "#94a3b8", marginBottom: 8, borderLeft: "3px solid #f59e0b", paddingLeft: 8 }}>推演控制</div>
                <label style={lbl}>最大智能体数 <span style={{ color: "#64748b" }}>— 从图谱生成的智能体数量上限</span></label>
                <input style={inp} type="number" min={1} max={50000} value={cfgMaxAgents} onChange={e => setCfgMaxAgents(Math.max(1, Number(e.target.value) || 1))} />
                <label style={lbl}>并发上限 <span style={{ color: "#64748b" }}>— 每轮同时发出的 LLM 请求数</span></label>
                <input style={inp} type="number" min={1} max={16} value={cfgMaxConcurrent} onChange={e => setCfgMaxConcurrent(Math.max(1, Number(e.target.value) || 1))} />
                <label style={lbl}>检索 Top-K <span style={{ color: "#64748b" }}>— 语义检索返回的文本片段数</span></label>
                <input style={inp} type="number" min={1} max={30} value={cfgRetrieveTopK} onChange={e => setCfgRetrieveTopK(Math.max(1, Number(e.target.value) || 1))} />
              </>
            )}

            <button onClick={saveConfig} disabled={cfgSaving} style={{ ...btn, width: "100%", background: "#3b82f6", color: "#fff", height: 36, fontSize: 14 }}>
              {cfgSaving ? "保存中..." : "保存配置"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
