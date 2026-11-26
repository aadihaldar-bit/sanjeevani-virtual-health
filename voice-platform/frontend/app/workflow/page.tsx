"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import {
  ReactFlow,
  MiniMap,
  Controls,
  Background,
  useNodesState,
  useEdgesState,
  Connection,
  Edge,
  ReactFlowProvider,
  useReactFlow,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from '@dagrejs/dagre';
import {
  ArrowLeft, Bot, Check, LayoutGrid, Loader2, Moon, PhoneForwarded, PhoneOff, Plus, Save, SlidersHorizontal, Sparkles, Sun, Ticket,
} from 'lucide-react';
import { nodeTypes } from '@/components/workflow/nodes';
import Sidebar from '@/components/workflow/Sidebar';
import EdgeEditor from '@/components/workflow/EdgeEditor';
import WorkflowSettings, { BuilderOptions, WorkflowSettingsValue, runsOnGeminiLive } from '@/components/workflow/WorkflowSettings';
import { BTN, BTN_PRIMARY, HELP, INPUT, LABEL, PANEL } from '@/components/workflow/styles';
import { Dialog } from '@/components/ui/Dialog';
import { STLLogo } from '@/components/STLLogo';
import { authFetch } from '@/lib/authFetch';
import { getApiBase } from '@/lib/api';
import { useThemeStore } from '@/lib/themeStore';
import { useWorkspace } from '@/lib/useMe';

const nodeWidth = 270;
const nodeHeight = 160;

/** How a path between two steps is drawn, for the current theme. */
function edgeLook(dark: boolean) {
  return {
    type: 'smoothstep',
    animated: false,
    style: { stroke: dark ? '#71717a' : '#a8a29e', strokeWidth: 1.5 },
    labelStyle: { fill: dark ? '#d4d4d8' : '#44403c', fontSize: 11, fontWeight: 500, fontFamily: 'inherit' },
    labelBgStyle: { fill: dark ? '#0f121a' : '#ffffff', stroke: dark ? '#27272a' : '#e7e5e4', strokeWidth: 1 },
    labelBgPadding: [8, 4] as [number, number],
    labelBgBorderRadius: 6,
  };
}

function getLayoutedElements(nodes: any[], edges: any[], dark: boolean, direction = 'LR') {
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir: direction, nodesep: 90, ranksep: 200, marginx: 80, marginy: 80 });
  nodes.forEach((node) => graph.setNode(node.id, { width: nodeWidth, height: nodeHeight }));
  edges.forEach((edge) => graph.setEdge(edge.source, edge.target));
  dagre.layout(graph);
  return {
    nodes: nodes.map((node) => {
      const at = graph.node(node.id);
      return at ? { ...node, position: { x: at.x - nodeWidth / 2, y: at.y - nodeHeight / 2 } } : node;
    }),
    edges: edges.map((edge) => ({ ...edge, ...edgeLook(dark) })),
  };
}

/** A blank agent: the call starts, one agent answers, the call ends. Nothing in it belongs to any company. */
const starterNodes = (): any[] => [
  { id: 'start', type: 'startNode', position: { x: 80, y: 180 }, data: { name: 'Start' } },
  {
    id: 'agent-1', type: 'agentNode', position: { x: 440, y: 140 },
    data: { name: 'Front desk', system_prompt: 'You answer calls for the company. Greet the caller warmly, find out what they need, and help them.', voice: '', llm: '', use_rag: true },
  },
  { id: 'end', type: 'endNode', position: { x: 840, y: 190 }, data: {} },
];
const starterEdges = (): Edge[] => [
  { id: 'e-start-agent', source: 'start', target: 'agent-1', label: 'Call connected' } as Edge,
  { id: 'e-agent-end', source: 'agent-1', target: 'end', label: 'Caller is done' } as Edge,
];

const GENERIC_PRESETS = [
  { title: 'Customer support', prompt: 'A customer support agent that finds out the problem, answers from the knowledge base, raises a ticket when it cannot solve it, and hands over to a person when the caller asks.' },
  { title: 'Appointment booking', prompt: 'A booking assistant that collects the caller’s name, contact, reason and preferred time, confirms the slot, and passes the details to the team.' },
  { title: 'Lead qualification', prompt: 'A sales assistant that asks a few qualifying questions about needs, budget and timeline, then books a call with the sales team for good leads.' },
  { title: 'Payment reminders', prompt: 'A polite agent that reminds callers about a due payment, answers questions about the amount and date, sends a payment link, and escalates disputes to a person.' },
];
const PLATFORM_PRESETS = [
  { title: 'Brother International CSR', prompt: 'A customer support multi-agent flow for Brother International printers with warranty verification, technical troubleshooting, service center locator and an escalation transfer step.' },
  { title: 'IT incident desk', prompt: 'An IT service desk agent that asks the right questions about an incident, searches the knowledge base, and raises an incident ticket.' },
];

const signatureOf = (name: string, settings: unknown, nodes: any[], edges: any[]) => JSON.stringify([
  name, settings,
  nodes.map((n) => [n.id, n.type, Math.round(n.position?.x ?? 0), Math.round(n.position?.y ?? 0), n.data]),
  edges.map((e: any) => [e.id, e.source, e.target, e.label || '', e.condition || '']),
]);

function WorkflowBuilderInner() {
  const { me, isPlatform, canManage } = useWorkspace();
  const theme = useThemeStore((s) => s.theme);
  const setTheme = useThemeStore((s) => s.setTheme);
  const toggleTheme = useThemeStore((s) => s.toggleTheme);
  const dark = theme === 'dark';
  const readOnly = !canManage;

  const [nodes, setNodes, onNodesChange] = useNodesState<any>(starterNodes());
  const [edges, setEdges, onEdgesChange] = useEdgesState<any>(starterEdges());
  const [selectedNode, setSelectedNode] = useState<any>(null);
  const [selectedEdge, setSelectedEdge] = useState<any>(null);
  const [workflowId, setWorkflowId] = useState<number | null>(null);
  const [workflowName, setWorkflowName] = useState('New agent');
  const [isActiveFlow, setIsActiveFlow] = useState(false);
  const [allWorkflows, setAllWorkflows] = useState<any[]>([]);
  const [options, setOptions] = useState<BuilderOptions | null>(null);
  const [settings, setSettings] = useState<WorkflowSettingsValue>({});
  const [settingsOpen, setSettingsOpen] = useState(false);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [saveErrors, setSaveErrors] = useState<string[]>([]);
  const [saveWarnings, setSaveWarnings] = useState<string[]>([]);
  const [savedSignature, setSavedSignature] = useState('');

  const [aiOpen, setAiOpen] = useState(false);
  const [aiPrompt, setAiPrompt] = useState('');
  const [generating, setGenerating] = useState(false);
  const [aiError, setAiError] = useState('');

  const { fitView } = useReactFlow();
  const signature = useMemo(() => signatureOf(workflowName, settings, nodes, edges), [workflowName, settings, nodes, edges]);
  const dirty = !loading && signature !== savedSignature;
  const signatureRef = useRef(signature);
  signatureRef.current = signature;

  // This page sits outside the console shell, so it restores the saved light/dark choice itself.
  useEffect(() => {
    try { setTheme(localStorage.getItem('stl_theme') === 'dark' ? 'dark' : 'light'); } catch { setTheme('light'); }
  }, [setTheme]);
  useEffect(() => { setEdges((eds) => eds.map((e) => ({ ...e, ...edgeLook(dark) }))); }, [dark, setEdges]);

  useEffect(() => {
    (async () => {
      try {
        const res = await authFetch(`${getApiBase()}/api/workflows/options`);
        if (res.ok) setOptions(await res.json());
      } catch (e) { console.error('Failed to load builder options', e); }
    })();
  }, []);

  const refit = (ms = 400) => setTimeout(() => fitView({ padding: 0.2, duration: ms }), 80);

  const loadAllWorkflows = useCallback(async () => {
    try {
      const res = await authFetch(`${getApiBase()}/api/workflows/`);
      if (res.ok) {
        const data = await res.json();
        setAllWorkflows(Array.isArray(data) ? data : []);
        return Array.isArray(data) ? data : [];
      }
    } catch (e) { console.error('Failed to load agents list', e); }
    return [];
  }, []);

  /** Puts a saved agent on the canvas, or the blank starter when there is nothing saved. */
  const applyWorkflowData = useCallback((data: any) => {
    const has = data && Array.isArray(data.nodes) && data.nodes.length > 0;
    const nextNodes = has ? data.nodes : starterNodes();
    const nextEdges = (has ? data.edges || [] : starterEdges()).map((e: any) => ({ ...e, ...edgeLook(useThemeStore.getState().theme === 'dark') }));
    const nextName = has ? data.name || 'Untitled agent' : 'New agent';
    const nextSettings = has ? data.settings || {} : {};
    setNodes(nextNodes);
    setEdges(nextEdges);
    setWorkflowName(nextName);
    setWorkflowId(has ? data.id ?? null : null);
    setSettings(nextSettings);
    setIsActiveFlow(has && !!data.is_active);
    setSelectedNode(null);
    setSelectedEdge(null);
    setSavedSignature(signatureOf(nextName, nextSettings, nextNodes, nextEdges));
    refit(300);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setNodes, setEdges, fitView]);

  const loadWorkflowById = useCallback(async (id: number) => {
    setLoadError('');
    try {
      const res = await authFetch(`${getApiBase()}/api/workflows/${id}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      applyWorkflowData(await res.json());
    } catch (e) {
      console.error('Failed to load agent', e);
      setLoadError('That agent could not be opened.');
    }
  }, [applyWorkflowData]);

  useEffect(() => {
    (async () => {
      const list = await loadAllWorkflows();
      const wanted = new URLSearchParams(window.location.search).get('id');
      try {
        const res = await authFetch(`${getApiBase()}/api/workflows/${wanted || 'active'}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data) applyWorkflowData(data);
        else if (list.length > 0 && !wanted) await loadWorkflowById(list[0].id);   // nothing is live yet: open the first agent
        else applyWorkflowData(null);
      } catch (e) {
        console.error('Failed to open the agent', e);
        setLoadError('The agent could not be opened. Check your connection and reload.');
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Warn before the tab closes with changes that were not saved.
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const confirmDiscard = () => !dirty || window.confirm('You have changes that are not saved. Leave them behind?');

  const handleNew = () => {
    if (!confirmDiscard()) return;
    applyWorkflowData(null);
    setWorkflowName('New agent');
  };

  const switchTo = (id: number) => { if (id !== workflowId && confirmDiscard()) loadWorkflowById(id); };

  const makeLive = async () => {
    if (!workflowId || readOnly) return;
    try {
      const res = await authFetch(`${getApiBase()}/api/workflows/${workflowId}/activate`, { method: 'POST' });
      if (res.ok) { setIsActiveFlow(true); loadAllWorkflows(); }
      else setSaveErrors(['This agent could not be made live.']);
    } catch { setSaveErrors(['This agent could not be made live. Check your connection.']); }
  };

  const onConnect = useCallback((params: Connection | Edge) => {
    setEdges((eds) => [...eds, { ...params, id: `e-${params.source}-${params.target}-${Date.now()}`, ...edgeLook(useThemeStore.getState().theme === 'dark') } as Edge]);
  }, [setEdges]);

  const onAutoLayout = useCallback(() => {
    const laid = getLayoutedElements(nodes, edges, dark);
    setNodes(laid.nodes);
    setEdges(laid.edges);
    refit(500);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, dark, setNodes, setEdges]);

  const updateEdgeData = (id: string, patch: { label?: string; condition?: string }) => {
    setEdges((eds) => eds.map((e) => (e.id === id ? ({ ...e, ...patch } as Edge) : e)));
    setSelectedEdge((cur: any) => (cur && cur.id === id ? { ...cur, ...patch } : cur));
  };
  const deleteEdge = (id: string) => { setEdges((eds) => eds.filter((e) => e.id !== id)); setSelectedEdge(null); };

  const nodeDisplayName = (id: string) => {
    const n: any = nodes.find((x: any) => x.id === id);
    return n?.data?.name || (n?.type === 'toolNode' ? 'Action' : n?.type === 'startNode' ? 'Call starts' : n?.type === 'endNode' ? 'End the call' : id);
  };

  const updateNodeData = (id: string, patch: any) => {
    setNodes((nds) => nds.map((n) => (n.id === id ? { ...n, data: { ...n.data, ...patch } } : n)));
    setSelectedNode((cur: any) => (cur && cur.id === id ? { ...cur, data: { ...cur.data, ...patch } } : cur));
  };

  /** New steps appear in the middle of what you are looking at, not at a random spot. */
  const addNode = (type: string, data: any) => {
    const base = nodes.reduce((m, n) => Math.max(m, n.position.x), 0);
    const id = `${type.replace('Node', '')}-${Date.now()}`;
    const node = { id, type, position: { x: base + 340, y: 120 + (nodes.length % 3) * 40 }, data };
    setNodes((nds) => [...nds, node]);
    setSelectedEdge(null);
    setSelectedNode(node);
    setTimeout(() => fitView({ padding: 0.2, duration: 400 }), 80);
  };
  const addAgent = () => addNode('agentNode', { name: 'New agent step', system_prompt: '', voice: '', llm: '', use_rag: true });
  const addTransfer = () => addNode('toolNode', { name: 'Hand over to a person', toolName: 'transfer_to_human' });
  const addTicket = () => addNode('toolNode', { name: 'Create a ticket', toolName: 'create_symphony_ticket' });
  const addEnd = () => addNode('endNode', {});

  const saveWorkflow = useCallback(async () => {
    if (readOnly || saving) return;
    setSaving(true);
    setSaveErrors([]);
    setSaveWarnings([]);
    // Saving must not swap the agent that is answering calls. A first agent goes live; any other stays as it is.
    const goLive = isActiveFlow || (workflowId === null && !allWorkflows.some((w) => w.is_active));
    const payload = {
      id: workflowId,
      name: workflowName.trim() || 'Untitled agent',
      is_active: goLive,
      settings,
      nodes: nodes.map((n) => ({ id: n.id, type: n.type, position: n.position, data: n.data })),
      edges: edges.map((e: any) => ({ id: e.id, source: e.source, target: e.target, label: e.label || '', condition: e.condition || '' })),
    };
    try {
      const res = await authFetch(`${getApiBase()}/api/workflows/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (res.ok) {
        const saved = await res.json();
        if (saved.id) setWorkflowId(saved.id);
        setIsActiveFlow(goLive);
        setSaveWarnings(saved.warnings || []);
        setSavedSignature(signatureRef.current);
        setSaveSuccess(true);
        setTimeout(() => setSaveSuccess(false), 2500);
        loadAllWorkflows();
      } else {
        let messages = [`Could not save (error ${res.status}).`];
        try {
          const detail = (await res.json())?.detail;
          if (detail?.errors?.length) messages = detail.errors;
          else if (typeof detail === 'string') messages = [detail];
        } catch { /* keep the generic message */ }
        setSaveErrors(messages);
      }
    } catch (err) {
      console.error(err);
      setSaveErrors(['Could not reach the server. Your changes were not saved.']);
    } finally {
      setSaving(false);
    }
  }, [readOnly, saving, isActiveFlow, workflowId, allWorkflows, workflowName, settings, nodes, edges, loadAllWorkflows]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); saveWorkflow(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [saveWorkflow]);

  const generate = async () => {
    if (!aiPrompt.trim()) return;
    setGenerating(true);
    setAiError('');
    try {
      const res = await authFetch(`${getApiBase()}/api/workflows/generate-ai`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt: aiPrompt.trim() }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!data.nodes || data.nodes.length === 0) throw new Error('empty');
      const laid = getLayoutedElements(data.nodes, data.edges || [], dark);
      setNodes(laid.nodes);
      setEdges(laid.edges);
      if (data.name) setWorkflowName(data.name);
      setSelectedNode(null);
      setSelectedEdge(null);
      setAiOpen(false);
      setAiPrompt('');
      refit(500);
    } catch (e) {
      console.error('Generate error', e);
      setAiError('Architect could not draw that one. Try describing it again with a little more detail.');
    } finally {
      setGenerating(false);
    }
  };

  const presets = isPlatform ? [...GENERIC_PRESETS, ...PLATFORM_PRESETS] : GENERIC_PRESETS;
  const status = !workflowId ? { label: 'Not saved yet', cls: 'bg-stone-100 text-stone-600 dark:bg-white/10 dark:text-stone-300' }
    : isActiveFlow ? { label: 'Live', cls: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300' }
      : { label: 'Draft', cls: 'bg-stone-100 text-stone-600 dark:bg-white/10 dark:text-stone-300' };

  return (
    <div className="flex flex-col h-screen w-screen bg-[#f7f7f5] dark:bg-[#08090e] text-stone-900 dark:text-stone-100 font-sans overflow-hidden">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5 bg-white dark:bg-[#0b0d13] border-b border-stone-200 dark:border-white/10 z-20">
        <Link href="/?view=console" className={BTN} onClick={(e) => { if (!confirmDiscard()) e.preventDefault(); }}>
          <ArrowLeft className="w-3.5 h-3.5" /> Console
        </Link>
        <span className="hidden md:block"><STLLogo /></span>
        <span className="hidden md:block h-5 w-px bg-stone-200 dark:bg-white/10" />

        <div className="flex items-center gap-2 min-w-0">
          {allWorkflows.length > 1 && (
            <select aria-label="Switch agent" value={workflowId ?? ''} onChange={(e) => e.target.value && switchTo(Number(e.target.value))}
              className="h-8 max-w-[180px] rounded-lg border border-stone-300 dark:border-white/15 bg-white dark:bg-white/5 px-2 text-[13px] cursor-pointer">
              {workflowId === null && <option value="">Unsaved agent</option>}
              {allWorkflows.map((w) => (<option key={w.id} value={w.id}>{w.is_active ? '● ' : ''}{w.name}</option>))}
            </select>
          )}
          <input aria-label="Agent name" type="text" value={workflowName} onChange={(e) => setWorkflowName(e.target.value)} readOnly={readOnly}
            className="h-8 w-56 max-w-full rounded-lg border border-transparent hover:border-stone-300 dark:hover:border-white/15 focus:border-indigo-500 bg-transparent px-2 text-[14px] font-semibold focus:outline-none focus:ring-2 focus:ring-indigo-500/20" />
          <span className={`px-2 py-0.5 rounded-full text-[11px] font-medium whitespace-nowrap ${status.cls}`}>{status.label}</span>
          {dirty && !readOnly && <span className="text-[12px] text-amber-600 dark:text-amber-400 whitespace-nowrap">Unsaved changes</span>}
        </div>

        <div className="flex flex-wrap items-center gap-2 ml-auto">
          <button onClick={onAutoLayout} className={BTN} title="Tidy the steps into neat columns"><LayoutGrid className="w-3.5 h-3.5" /> Tidy up</button>
          <button onClick={() => setSettingsOpen(true)} className={BTN}><SlidersHorizontal className="w-3.5 h-3.5" /> Settings</button>
          {!readOnly && <button onClick={() => { setAiError(''); setAiOpen(true); }} className={BTN}><Sparkles className="w-3.5 h-3.5 text-indigo-500" /> Draw with AI</button>}
          {!readOnly && <button onClick={handleNew} className={BTN}><Plus className="w-3.5 h-3.5" /> New</button>}
          <button onClick={toggleTheme} className={`${BTN} !px-2`} aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}>
            {dark ? <Sun className="w-3.5 h-3.5" /> : <Moon className="w-3.5 h-3.5" />}
          </button>
          {!readOnly && workflowId && !isActiveFlow && (
            <button onClick={makeLive} disabled={dirty} className={BTN} title={dirty ? 'Save your changes first' : 'Let this agent answer the phone'}>Make live</button>
          )}
          {!readOnly && (
            <button onClick={saveWorkflow} disabled={saving || (!dirty && !!workflowId)} className={BTN_PRIMARY} title="Ctrl+S">
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : saveSuccess ? <Check className="w-3.5 h-3.5" /> : <Save className="w-3.5 h-3.5" />}
              {saving ? 'Saving' : saveSuccess ? 'Saved' : 'Save'}
            </button>
          )}
        </div>
      </header>

      {me && readOnly && (
        <div className="px-4 py-2 text-[13px] bg-stone-100 dark:bg-white/5 text-stone-600 dark:text-stone-300 border-b border-stone-200 dark:border-white/10">
          You can look around, but only an administrator can change an agent.
        </div>
      )}
      {loadError && (
        <div role="alert" className="mx-4 mt-3 px-3 py-2 rounded-lg text-[13px] border border-rose-200 dark:border-rose-500/30 bg-rose-50 dark:bg-rose-500/10 text-rose-800 dark:text-rose-200">{loadError}</div>
      )}
      {(saveErrors.length > 0 || saveWarnings.length > 0) && (
        <div role="alert" className={`mx-4 mt-3 px-3 py-2 rounded-lg text-[13px] border ${saveErrors.length > 0
          ? 'border-rose-200 dark:border-rose-500/30 bg-rose-50 dark:bg-rose-500/10 text-rose-800 dark:text-rose-200'
          : 'border-amber-200 dark:border-amber-500/30 bg-amber-50 dark:bg-amber-500/10 text-amber-900 dark:text-amber-200'}`}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="font-medium mb-1">{saveErrors.length > 0 ? 'This agent could not be saved' : 'Saved, with a few things to look at'}</p>
              <ul className="list-disc pl-4 space-y-0.5">{(saveErrors.length > 0 ? saveErrors : saveWarnings).map((m, i) => (<li key={i}>{m}</li>))}</ul>
            </div>
            <button onClick={() => { setSaveErrors([]); setSaveWarnings([]); }} className="opacity-70 hover:opacity-100 cursor-pointer" aria-label="Dismiss">✕</button>
          </div>
        </div>
      )}

      <div className="flex-1 flex relative overflow-hidden">
        <div className="flex-grow h-full relative">
          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-[#f7f7f5]/80 dark:bg-[#08090e]/80 text-[13px] text-stone-500 dark:text-stone-400">
              <Loader2 className="w-4 h-4 animate-spin mr-2" /> Opening your agent...
            </div>
          )}
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={(_e, node) => { setSelectedEdge(null); setSelectedNode(node); }}
            onEdgeClick={(_e, edge) => { setSelectedNode(null); setSelectedEdge(edge); }}
            onPaneClick={() => { setSelectedNode(null); setSelectedEdge(null); }}
            nodeTypes={nodeTypes}
            nodesDraggable={!readOnly}
            nodesConnectable={!readOnly}
            deleteKeyCode={readOnly ? null : ['Backspace', 'Delete']}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            colorMode={dark ? 'dark' : 'light'}
            proOptions={{ hideAttribution: true }}
          >
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable nodeColor={dark ? '#3f3f46' : '#d6d3d1'} maskColor={dark ? 'rgba(8,9,14,0.7)' : 'rgba(247,247,245,0.7)'} className="!hidden md:!block" />
            <Background color={dark ? '#1f2330' : '#e0dfdb'} gap={22} size={1.2} />
          </ReactFlow>

          {!readOnly && (
            <div role="toolbar" aria-label="Add a step"
              className="absolute bottom-5 left-1/2 -translate-x-1/2 z-10 flex flex-wrap items-center justify-center gap-1 p-1.5 rounded-xl border border-stone-200 dark:border-white/10 bg-white/95 dark:bg-[#0f121a]/95 backdrop-blur shadow-lg max-w-[calc(100%-1.5rem)]">
              <span className="px-2 text-[12px] text-stone-500 dark:text-stone-400">Add a step</span>
              {[
                { label: 'Agent', icon: <Bot className="w-3.5 h-3.5" strokeWidth={1.75} />, run: addAgent },
                { label: 'Hand over', icon: <PhoneForwarded className="w-3.5 h-3.5" strokeWidth={1.75} />, run: addTransfer },
                { label: 'Ticket', icon: <Ticket className="w-3.5 h-3.5" strokeWidth={1.75} />, run: addTicket },
                { label: 'End call', icon: <PhoneOff className="w-3.5 h-3.5" strokeWidth={1.75} />, run: addEnd },
              ].map((b) => (
                <button key={b.label} onClick={b.run}
                  className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg text-[13px] text-stone-700 dark:text-stone-200 hover:bg-stone-100 dark:hover:bg-white/10 cursor-pointer">
                  {b.icon} {b.label}
                </button>
              ))}
            </div>
          )}
        </div>

        {selectedEdge && !selectedNode && (
          <aside className={PANEL}>
            <fieldset disabled={readOnly} className="contents">
              <EdgeEditor edge={selectedEdge} sourceName={nodeDisplayName(selectedEdge.source)} targetName={nodeDisplayName(selectedEdge.target)}
                onChange={updateEdgeData} onDelete={deleteEdge} />
            </fieldset>
          </aside>
        )}
        {selectedNode && (
          <aside className={PANEL}>
            <fieldset disabled={readOnly} className="contents">
              <Sidebar node={selectedNode} updateNode={updateNodeData} options={options} ttsProvider={settings.tts?.provider} geminiLive={runsOnGeminiLive(options, workflowId)} />
            </fieldset>
          </aside>
        )}
      </div>

      {settingsOpen && <WorkflowSettings options={options} value={settings} onChange={setSettings} onClose={() => setSettingsOpen(false)} workflowId={workflowId} />}

      <Dialog open={aiOpen} onClose={() => !generating && setAiOpen(false)} width="max-w-2xl" title="Draw an agent with AI"
        description="Describe the calls this agent should handle. Architect lays out the steps and paths, and you can change anything afterwards."
        footer={(
          <>
            <button onClick={() => setAiOpen(false)} disabled={generating} className={BTN}>Cancel</button>
            <button onClick={generate} disabled={!aiPrompt.trim() || generating} className={BTN_PRIMARY}>
              {generating ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Drawing...</> : <><Sparkles className="w-3.5 h-3.5" /> Draw it</>}
            </button>
          </>
        )}>
        <div className="space-y-4 pb-3">
          <div>
            <p className={LABEL}>Start from an idea</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {presets.map((p) => (
                <button key={p.title} onClick={() => setAiPrompt(p.prompt)}
                  className="p-2.5 text-left rounded-lg border border-stone-200 dark:border-white/10 hover:border-indigo-300 dark:hover:border-indigo-400/40 hover:bg-indigo-50/40 dark:hover:bg-indigo-500/5 cursor-pointer">
                  <span className="block text-[13px] font-medium text-stone-900 dark:text-white">{p.title}</span>
                  <span className="block mt-0.5 text-[11.5px] text-stone-500 dark:text-stone-400 line-clamp-2">{p.prompt}</span>
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className={LABEL} htmlFor="ai-prompt">What should it do?</label>
            <textarea id="ai-prompt" value={aiPrompt} onChange={(e) => setAiPrompt(e.target.value)} rows={4} className={`${INPUT} resize-none leading-relaxed`}
              placeholder="e.g. A receptionist for a dental clinic that books and reschedules visits, answers questions about fees, and hands over to the front desk for emergencies." />
            <p className={HELP}>Drawing replaces what is on the canvas. You can still undo it by leaving without saving.</p>
          </div>
          {aiError && <p role="alert" className="text-[13px] text-rose-600 dark:text-rose-400">{aiError}</p>}
        </div>
      </Dialog>
    </div>
  );
}

export default function WorkflowBuilder() {
  return (
    <ReactFlowProvider>
      <WorkflowBuilderInner />
    </ReactFlowProvider>
  );
}
