'use client';

import React, { useEffect, useState } from 'react';
import { Bot, Plus, Trash2, ArrowRight } from 'lucide-react';
import { useWorkspace } from '@/lib/useMe';
import { Badge, Button, Card, EmptyState, Page, Skeleton } from '@/components/ui/kit';
import { useShellStore } from '@/lib/shellStore';
import type { TemplateInfo } from '@/components/agents/NewAgentDialog';
import { authFetch } from '@/lib/authFetch';

import { getApiBase } from '@/lib/api';

interface AgentCard {
  id: number;
  name: string;
  description: string;
  is_active: boolean;
  company?: string | null;
  tenant_id?: number | null;
  /** Numbers given to this agent: calls to them reach it whether or not it is the live one. */
  numbers?: string[];
  voice_model: string;
  voice_name: string;
  languages: string[];
  node_count: number;
  edge_count: number;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** Agent names can carry non-breaking hyphens ("Global E‑Commerce 12‑Agent"): show plain ones so they wrap and match like any other. */
const plain = (name: string) => name.replace(/[‐‑]/g, '-');

const LANGUAGE_NAME: Record<string, string> = { 'en-IN': 'English', 'hi-IN': 'Hindi', 'mr-IN': 'Marathi', 'ta-IN': 'Tamil', 'te-IN': 'Telugu' };

/** Numbers of the same company that are given to another agent: the live agent does not answer them. */
function heldByOthers(agent: AgentCard, all: AgentCard[]) {
  return all
    .filter((other) => other.id !== agent.id && other.tenant_id === agent.tenant_id)
    .flatMap((other) => (other.numbers || []).map((number) => ({ number, agent: plain(other.name) })));
}

export function AgentsView() {
  const { canManage, isPlatform, workspaceName } = useWorkspace();
  const [agents, setAgents] = useState<AgentCard[]>([]);
  const [loading, setLoading] = useState(true);
  const { openNewAgent, agentsChanged, agentsVersion } = useShellStore();
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);

  async function loadAgents() {
    setLoading(true);
    try {
      const res = await authFetch(`${getApiBase()}/api/agents/`);
      const data = await res.json();
      setAgents(Array.isArray(data) ? data : []);
    } catch {
      setAgents([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadAgents(); }, [agentsVersion]);

  useEffect(() => {
    if (!canManage) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await authFetch(`${getApiBase()}/api/agents/templates`);
        if (res.ok && !cancelled) setTemplates((await res.json()).templates || []);
      } catch { /* the gallery just stays empty */ }
    })();
    return () => { cancelled = true; };
  }, [canManage]);

  async function handleDelete(agent: AgentCard) {
    const live = agent.is_active ? ' It is the live agent, so no agent will be live until you make another one live.' : '';
    if (!window.confirm(`Delete "${plain(agent.name)}"?${live} This cannot be undone.`)) return;
    await authFetch(`${getApiBase()}/api/agents/${agent.id}`, { method: 'DELETE' });
    agentsChanged();
  }

  async function handleDeactivate(agent: AgentCard) {
    const who = agent.company ? `for ${agent.company}` : 'for your company';
    const held = (agent.numbers || []).length > 0
      ? ` It will still answer ${agent.numbers!.join(', ')}, because ${agent.numbers!.length === 1 ? 'that number is' : 'those numbers are'} given to it (change that in Settings > Phone numbers).`
      : '';
    if (!window.confirm(`Take "${plain(agent.name)}" offline? Calls ${who} will have no agent to answer them until you make an agent live again.${held}`)) return;
    try {
      await authFetch(`${getApiBase()}/api/agents/${agent.id}/deactivate`, { method: 'POST' });
      agentsChanged();
    } catch (e) {
      console.error('Failed to take the agent offline', e);
    }
  }

  async function handleActivate(id: number) {
    try {
      await authFetch(`${getApiBase()}/api/agents/${id}/activate`, { method: 'POST' });
      agentsChanged();
    } catch (e) {
      console.error('Failed to activate agent', e);
    }
  }

  return (
    <>
      <Page
        title="Agents"
        subtitle={isPlatform ? 'Voice agents that answer calls. Each company has one live agent at a time, so you see one per company here.' : `Voice agents for ${workspaceName}. One is live at a time.`}
        actions={canManage ? <Button variant="primary" onClick={() => openNewAgent()}><Plus className="w-4 h-4" /> New agent</Button> : undefined}
      >
        {loading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
            {[1, 2, 3].map((i) => <Skeleton key={i} className="h-44 rounded-xl" />)}
          </div>
        ) : agents.length === 0 ? (
          <Card>
            <EmptyState
              icon={<Bot className="w-5 h-5" strokeWidth={1.75} />}
              title={isPlatform ? 'No agents yet' : `${workspaceName} has no agents yet`}
              text={canManage
                ? (isPlatform
                  ? 'Create your first voice agent from a template or a custom prompt.'
                  : 'This is your own blank workspace. Start from scratch with a template or a custom prompt; what you build here belongs to your organization only.')
                : 'Your administrator has not set up any agents yet.'}
              action={canManage ? <Button variant="primary" onClick={() => openNewAgent()}><Plus className="w-4 h-4" /> Start from scratch</Button> : undefined}
            />
          </Card>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
            {agents.map((agent) => (
              <div key={agent.id} className="rounded-xl border border-stone-200 dark:border-white/10 bg-white dark:bg-[#0f121a] p-5 flex flex-col hover:border-stone-300 dark:hover:border-white/20 transition-colors">
                <div className="flex items-start justify-between gap-3">
                  <span className="w-10 h-10 rounded-lg bg-stone-100 dark:bg-white/5 flex items-center justify-center text-stone-500 flex-shrink-0"><Bot className="w-5 h-5" strokeWidth={1.75} /></span>
                  {agent.is_active ? <Badge tone="green">Live</Badge> : <Badge>Not live</Badge>}
                </div>
                {isPlatform && agent.company && <p className="mt-3 -mb-2 text-[11px] font-medium uppercase tracking-wide text-stone-500 dark:text-stone-400">{agent.company}</p>}
                <h3 className="mt-3 text-[14px] font-semibold text-stone-900 dark:text-white truncate" title={plain(agent.name)}>{plain(agent.name)}</h3>
                <p className="mt-0.5 text-[12px] text-stone-500 dark:text-stone-400">
                  {agent.voice_name || 'Default voice'} · {plural(agent.node_count, 'step')} · {plural(agent.edge_count, 'transition')}
                </p>
                {agent.languages?.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {agent.languages.map((lang) => <Badge key={lang}>{LANGUAGE_NAME[lang] || lang}</Badge>)}
                  </div>
                )}
                {(agent.numbers || []).length > 0 && (
                  <p className="mt-3 text-[12px] text-stone-600 dark:text-stone-300">
                    <span className="font-medium">Answers {agent.numbers!.join(', ')}</span>
                    {!agent.is_active && <span className="block mt-0.5 text-amber-700 dark:text-amber-400">Calls to {agent.numbers!.length === 1 ? 'this number' : 'these numbers'} reach this agent even though it is not live.</span>}
                  </p>
                )}
                {agent.is_active && heldByOthers(agent, agents).map(({ number, agent: other }) => (
                  <p key={number} className="mt-2 text-[12px] text-amber-700 dark:text-amber-400">{number} is given to {other}, so calls to it do not reach this agent.</p>
                ))}
                <div className="mt-5 pt-4 border-t border-stone-100 dark:border-white/5 flex items-center gap-2">
                  <a
                    href={`/workflow?id=${agent.id}`}
                    className="inline-flex items-center justify-center gap-1.5 h-9 px-3.5 rounded-lg border border-stone-300 dark:border-white/15 bg-white dark:bg-white/5 text-[13px] font-medium text-stone-800 dark:text-stone-100 hover:bg-stone-50 dark:hover:bg-white/10"
                  >
                    {canManage ? 'Open builder' : 'View'} <ArrowRight className="w-3.5 h-3.5" />
                  </a>
                  {canManage && !agent.is_active && (
                    <Button variant="ghost" onClick={() => handleActivate(agent.id)}>Make live</Button>
                  )}
                  {canManage && agent.is_active && (
                    <Button variant="ghost" onClick={() => handleDeactivate(agent)}>Take offline</Button>
                  )}
                  {canManage && (
                    <button
                      onClick={() => handleDelete(agent)}
                      className="ml-auto p-2 rounded-lg text-stone-400 hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-500/10 cursor-pointer"
                      title="Delete agent"
                      aria-label={`Delete ${plain(agent.name)}`}
                    >
                      <Trash2 className="w-4 h-4" strokeWidth={1.75} />
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {canManage && templates.length > 0 && (
          <section aria-labelledby="gallery-title">
            <div className="flex items-end justify-between gap-3 mb-3">
              <div>
                <h3 id="gallery-title" className="text-[15px] font-semibold text-stone-900 dark:text-white">Start from a ready-made workflow</h3>
                <p className="text-[13px] text-stone-500 dark:text-stone-400">Pick one, give it a name, and change anything in the builder.</p>
              </div>
              <button onClick={() => openNewAgent()} className="text-[13px] text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer whitespace-nowrap">See all options</button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
              {templates.slice(0, 6).map((tpl) => (
                <button key={tpl.id} onClick={() => openNewAgent(tpl.id)}
                  className="text-left rounded-xl border border-stone-200 dark:border-white/10 bg-white dark:bg-[#0f121a] p-5 hover:border-indigo-300 dark:hover:border-indigo-400/40 hover:shadow-sm transition-all cursor-pointer">
                  <span className="flex items-start justify-between gap-2">
                    <span className="text-[14px] font-semibold text-stone-900 dark:text-white">{tpl.title}</span>
                    <Badge>{tpl.category}</Badge>
                  </span>
                  <span className="block mt-1.5 text-[13px] leading-relaxed text-stone-500 dark:text-stone-400">{tpl.summary}</span>
                  <span className="mt-3 flex flex-wrap gap-1.5">{tpl.features.map((f) => <Badge key={f} tone="blue">{f}</Badge>)}</span>
                </button>
              ))}
            </div>
          </section>
        )}
      </Page>
    </>
  );
}
