'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { ArrowUpRight, Bot, Check, FileText, Sparkles } from 'lucide-react';
import { authFetch } from '@/lib/authFetch';
import { getApiBase } from '@/lib/api';
import { useShellStore } from '@/lib/shellStore';
import { Badge, Button, Skeleton } from '@/components/ui/kit';
import { Dialog, FormField, inputClass } from '@/components/ui/Dialog';

export interface TemplateInfo {
  id: string;
  title: string;
  category: string;
  summary: string;
  features: string[];
  agents: number;
  steps: number;
  languages: string[];
}

const BLANK = 'blank';

/** Pick how to start (Architect, a blank page, or a ready-made workflow), name it, create it. */
export function NewAgentDialog() {
  const { newAgentOpen, newAgentPreset, closeNewAgent, openArchitect, agentsChanged } = useShellStore();
  const [templates, setTemplates] = useState<TemplateInfo[] | null>(null);
  const [category, setCategory] = useState('All');
  const [choice, setChoice] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ id: number; name: string } | null>(null);

  useEffect(() => {
    if (!newAgentOpen) return;
    setChoice(newAgentPreset); setCategory('All'); setError(null); setCreated(null); setBusy(false);
    let cancelled = false;
    (async () => {
      try {
        const res = await authFetch(`${getApiBase()}/api/agents/templates`);
        const data = res.ok ? await res.json() : { templates: [] };
        if (!cancelled) setTemplates(data.templates || []);
      } catch { if (!cancelled) setTemplates([]); }
    })();
    return () => { cancelled = true; };
  }, [newAgentOpen, newAgentPreset]);

  const picked = useMemo(() => templates?.find((t) => t.id === choice) || null, [templates, choice]);
  useEffect(() => { setName(picked ? picked.title : choice === BLANK ? 'My agent' : ''); }, [picked, choice]);

  const categories = useMemo(() => ['All', ...Array.from(new Set((templates || []).map((t) => t.category)))], [templates]);
  const shown = (templates || []).filter((t) => category === 'All' || t.category === category);

  async function create() {
    if (!choice || !name.trim() || busy) return;
    setBusy(true); setError(null);
    try {
      const res = await authFetch(`${getApiBase()}/api/agents/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), template: choice }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) { setError(typeof data?.detail === 'string' ? data.detail : `Could not create the agent (${res.status}).`); return; }
      agentsChanged();
      setCreated({ id: data.id, name: data.name || name.trim() });
    } catch {
      setError('Could not reach the server.');
    } finally {
      setBusy(false);
    }
  }

  if (created && newAgentOpen) {
    return (
      <Dialog open title="Agent created" onClose={closeNewAgent} footer={<Button onClick={closeNewAgent}>Done</Button>}>
        <div className="py-6 text-center">
          <span className="mx-auto w-11 h-11 rounded-full bg-emerald-50 dark:bg-emerald-500/10 flex items-center justify-center text-emerald-600"><Check className="w-5 h-5" strokeWidth={2.5} /></span>
          <p className="mt-3 text-[15px] font-semibold text-stone-900 dark:text-white break-words">{created.name}</p>
          <p className="mt-1 text-[13px] text-stone-500 dark:text-stone-400">It is ready to edit. It is not live yet: try it in the simulator, then make it live from the Agents page.</p>
          <a href={`/workflow?id=${created.id}`} className="mt-5 inline-flex items-center gap-1.5 h-9 px-4 rounded-lg bg-indigo-600 text-white text-[13px] font-medium hover:bg-indigo-500">
            Open in the builder <ArrowUpRight className="w-3.5 h-3.5" />
          </a>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog
      open={newAgentOpen}
      title="Create an agent"
      description="Start from a ready-made workflow, a blank page, or describe it to Architect."
      onClose={closeNewAgent}
      width="max-w-3xl"
      footer={choice ? (
        <div className="flex w-full items-end gap-3">
          <div className="flex-1 min-w-0">
            <FormField label="Agent name"><input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} maxLength={80} /></FormField>
          </div>
          <Button variant="primary" onClick={create} disabled={!name.trim() || busy}>{busy ? 'Creating…' : 'Create agent'}</Button>
        </div>
      ) : undefined}
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <button onClick={() => { closeNewAgent(); openArchitect(); }} className="text-left rounded-xl border border-indigo-200 dark:border-indigo-400/30 bg-indigo-50/60 dark:bg-indigo-500/10 p-4 hover:bg-indigo-50 dark:hover:bg-indigo-500/15 cursor-pointer">
          <span className="flex items-center gap-2 text-[14px] font-semibold text-indigo-900 dark:text-indigo-100"><Sparkles className="w-4 h-4" /> Build with Architect</span>
          <span className="block mt-1 text-[12.5px] text-indigo-900/70 dark:text-indigo-100/70">Describe what you need in your own words and Architect sets it up. Best if you are not sure where to start.</span>
        </button>
        <button
          onClick={() => setChoice(BLANK)}
          aria-pressed={choice === BLANK}
          className={`text-left rounded-xl border p-4 cursor-pointer transition-colors ${choice === BLANK ? 'border-indigo-500 ring-1 ring-indigo-500 bg-indigo-50/40 dark:bg-indigo-500/10' : 'border-stone-200 dark:border-white/10 hover:bg-stone-50 dark:hover:bg-white/5'}`}
        >
          <span className="flex items-center gap-2 text-[14px] font-semibold text-stone-900 dark:text-white"><FileText className="w-4 h-4" /> Blank agent</span>
          <span className="block mt-1 text-[12.5px] text-stone-500 dark:text-stone-400">One simple agent and a way to hand over to a person. You write the rest in the builder.</span>
        </button>
      </div>

      <div className="mt-6 mb-3 flex items-center justify-between gap-3 flex-wrap">
        <p className="text-[13px] font-semibold text-stone-900 dark:text-white">Ready-made workflows</p>
        <div role="group" aria-label="Category" className="flex gap-1.5 flex-wrap">
          {categories.map((c) => (
            <button key={c} onClick={() => setCategory(c)} aria-pressed={category === c}
              className={`h-7 px-3 rounded-full text-[12px] font-medium cursor-pointer ${category === c ? 'bg-stone-900 text-white dark:bg-white dark:text-stone-900' : 'bg-stone-100 dark:bg-white/5 text-stone-600 dark:text-stone-300 hover:bg-stone-200 dark:hover:bg-white/10'}`}>
              {c}
            </button>
          ))}
        </div>
      </div>

      {templates === null ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pb-2">{[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-28 rounded-xl" />)}</div>
      ) : shown.length === 0 ? (
        <p className="py-6 text-center text-[13px] text-stone-500">No ready-made workflows to show. Start from a blank agent or ask Architect.</p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pb-2">
          {shown.map((t) => (
            <button
              key={t.id}
              onClick={() => setChoice(t.id)}
              aria-pressed={choice === t.id}
              className={`text-left rounded-xl border p-4 cursor-pointer transition-colors ${choice === t.id ? 'border-indigo-500 ring-1 ring-indigo-500 bg-indigo-50/40 dark:bg-indigo-500/10' : 'border-stone-200 dark:border-white/10 hover:bg-stone-50 dark:hover:bg-white/5'}`}
            >
              <span className="flex items-start justify-between gap-2">
                <span className="flex items-center gap-2 text-[14px] font-semibold text-stone-900 dark:text-white"><Bot className="w-4 h-4 text-stone-400" strokeWidth={1.75} />{t.title}</span>
                <Badge>{t.category}</Badge>
              </span>
              <span className="block mt-1.5 text-[12.5px] leading-relaxed text-stone-500 dark:text-stone-400">{t.summary}</span>
              <span className="mt-2.5 flex flex-wrap gap-1.5">
                {t.features.map((f) => <Badge key={f} tone="blue">{f}</Badge>)}
              </span>
            </button>
          ))}
        </div>
      )}
      {error && <p role="alert" className="mt-3 text-[13px] text-rose-600 dark:text-rose-400">{error}</p>}
    </Dialog>
  );
}
