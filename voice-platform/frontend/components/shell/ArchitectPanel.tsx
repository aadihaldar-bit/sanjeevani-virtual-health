'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUp, ArrowUpRight, Plus, Sparkles, Square, X } from 'lucide-react';
import { authFetch } from '@/lib/authFetch';
import { getApiBase } from '@/lib/api';
import { useDashboardStore } from '@/lib/store';
import { useShellStore } from '@/lib/shellStore';
import type { TabId } from '@/lib/store';
import { applyEvent, endRun, newRun, readEvents, type RunEvent, type RunView } from '@/lib/architectRun';
import { Checklist } from './architect/Checklist';
import { KnowledgeList } from './architect/KnowledgeList';
import { BTN, BTN_OUTLINE, BTN_QUIET, SpeakingCard, type Outcome, type SpeakingProposal } from './architect/SpeakingCard';
import { ThinkingBlock } from './architect/Thinking';

interface Message {
  id: number;
  role: 'user' | 'assistant';
  text: string;
  /** what Architect did in answer: its thinking, its checklist, how it ended */
  run?: RunView;
  /** what was made, in a sentence, so the next question to the model knows it */
  note?: string;
  /** a change of voice the person was asked to confirm */
  outcome?: Outcome;
  /** the plain agent made when the design could not be */
  plain?: { state: 'creating' | 'created' | 'failed'; agentId?: number; error?: string };
}

const SUGGESTIONS = [
  'A front desk for my hospital with appointment, billing and pharmacy desks',
  'Write a fees and refunds document for my clinic',
  'Show me what is in my knowledge',
  'Take appointment bookings for my clinic',
];

const GREETING = "Hi, I'm Architect. Tell me what you want in your own words. I'll think it through, write a checklist and do it step by step: designing the agent, writing the documents it reads, and creating it. You can ask me to show or write your knowledge documents, or how anything in the console works.";

// The conversation survives closing and reopening the panel (not a page reload). It is the one copy: every change goes through it.
let saved: Message[] = [];
let nextId = 1;
let working: AbortController | null = null;       // the run in progress, so it can be stopped from a panel opened later

const detailOf = (data: unknown, fallback: string): string => {
  const d = (data as { detail?: unknown } | null)?.detail;
  return typeof d === 'string' ? d : fallback;
};

async function postJson(path: string, body: unknown): Promise<{ ok: boolean; status: number; data: any }> {
  const res = await authFetch(`${getApiBase()}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return { ok: res.ok, status: res.status, data: await res.json().catch(() => null) };
}

/** What the model is told of the conversation: what was said, and for Architect's turns what it made. */
const asHistory = (all: Message[]) => all.filter((m) => m.text.trim()).slice(-20)
  .map((m) => ({ role: m.role, content: (m.note ? `${m.text}\n(${m.note})` : m.text).slice(0, 2000) }));

const isBusy = (all: Message[]) => all.some((m) => m.run && (m.run.phase === 'thinking' || m.run.phase === 'working'));

/** A chat that works: it thinks aloud, writes a checklist and does the items one at a time. */
export function ArchitectPanel() {
  const { closeArchitect, agentsChanged, pendingPrompt, clearPrompt } = useShellStore();
  const { setSelectedTab } = useDashboardStore();
  const [messages, setMessages] = useState<Message[]>(saved);
  const [draft, setDraft] = useState('');
  const scroller = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const stick = useRef(true);
  const busy = isBusy(messages);

  const update = useCallback((fn: (m: Message[]) => Message[]) => {
    saved = fn(saved);
    setMessages(saved);
  }, []);
  const patch = useCallback((id: number, fn: (m: Message) => Message) => update((all) => all.map((x) => (x.id === id ? fn(x) : x))), [update]);

  useEffect(() => { input.current?.focus(); }, []);
  useEffect(() => {                                   // follows the newest line, unless the person has scrolled up to read
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // The newest thing, in words, for a screen reader: the chat itself is not read out as it streams.
  const announcement = useMemo(() => {
    const run = [...messages].reverse().find((m) => m.run)?.run;
    if (!run) return '';
    const doing = run.steps.find((s) => s.status === 'running');
    if (doing) return `Working on: ${doing.title}`;
    if (run.end) return run.end.summary;
    return run.error ?? (run.phase === 'thinking' ? 'Architect is thinking' : '');
  }, [messages]);

  const handle = useCallback((id: number, event: RunEvent) => {
    patch(id, (m) => (m.run ? {
      ...m,
      run: applyEvent(m.run, event, Date.now()),
      text: event.t === 'reply' ? event.text : m.text,
      note: event.t === 'done' ? event.note : m.note,
    } : m));
    if (event.t === 'done' && event.agent_id) agentsChanged();
  }, [patch, agentsChanged]);

  const send = useCallback(async (text: string) => {
    const content = text.trim().slice(0, 2000);
    if (!content || isBusy(saved)) return;
    const asked: Message = { id: nextId++, role: 'user', text: content };
    const answerId = nextId++;
    const history = asHistory([...saved, asked]);
    update((all) => [...all, asked, { id: answerId, role: 'assistant', text: '', run: newRun(Date.now()) }]);
    setDraft('');
    stick.current = true;
    const controller = new AbortController();
    working = controller;
    try {
      const res = await authFetch(`${getApiBase()}/api/architect/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: history }),
        signal: controller.signal,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        patch(answerId, (m) => ({ ...m, run: undefined, text: res.status === 403 ? 'Only administrators can build with Architect. Ask your administrator to set this up.' : detailOf(data, 'Something went wrong.') }));
        return;
      }
      let finished = false;
      for await (const event of readEvents(res)) {
        if (event.t === 'done' || event.t === 'error') finished = true;
        handle(answerId, event);
      }
      if (!finished) patch(answerId, (m) => (m.run ? { ...m, run: endRun(m.run, 'lost') } : m));
    } catch {
      patch(answerId, (m) => (m.run ? { ...m, run: endRun(m.run, controller.signal.aborted ? 'stopped' : 'lost') } : m));
    } finally {
      if (working === controller) working = null;
    }
  }, [update, patch, handle]);

  // Something outside (the search palette) asked Architect a question.
  useEffect(() => {
    if (pendingPrompt) { const q = pendingPrompt; clearPrompt(); void send(q); }
  }, [pendingPrompt, clearPrompt, send]);

  const saving = messages.some((m) => m.run?.steps.some((s) => (s.id === 'save' || s.id === 'build') && s.status === 'running'));
  const stop = () => { if (!saving) working?.abort(); };            // saving is one step that is never left half done

  /** Ask again: the failed turn is taken out, so the question is not in the chat twice. */
  function retry(answerId: number) {
    const at = saved.findIndex((m) => m.id === answerId);
    const asked = at > 0 ? saved[at - 1] : undefined;
    if (!asked || asked.role !== 'user') return;
    update((all) => all.filter((m) => m.id !== answerId && m.id !== asked.id));
    void send(asked.text);
  }

  async function createPlain(answerId: number, fallback: { name: string; prompt: string }) {
    patch(answerId, (m) => ({ ...m, plain: { state: 'creating' } }));
    try {
      const { ok, status, data } = await postJson('/api/agents/', { name: fallback.name, template: 'custom_ai', prompt: fallback.prompt });
      if (!ok) throw new Error(detailOf(data, `Could not create the agent (${status}).`));
      agentsChanged();
      patch(answerId, (m) => ({ ...m, plain: { state: 'created', agentId: data.id } }));
    } catch (e) {
      patch(answerId, (m) => ({ ...m, plain: { state: 'failed', error: e instanceof Error ? e.message : 'Could not create the agent.' } }));
    }
  }

  async function applySpeaking(answerId: number, proposal: SpeakingProposal) {
    patch(answerId, (m) => ({ ...m, outcome: { state: 'creating' } }));
    try {
      const { ok, status, data } = await postJson('/api/architect/speaking', { agent_id: proposal.agent_id, ...proposal.changes });
      if (!ok) throw new Error(detailOf(data, `Could not change it (${status}).`));
      agentsChanged();
      patch(answerId, (m) => ({ ...m, outcome: { state: 'created' } }));
    } catch (e) {
      patch(answerId, (m) => ({ ...m, outcome: { state: 'failed', error: e instanceof Error ? e.message : 'Could not change it.' } }));
    }
  }

  function newChat() { if (busy) return; saved = []; setMessages([]); setDraft(''); input.current?.focus(); }

  return (
    <aside role="complementary" aria-label="Architect" className="w-full sm:w-[420px] flex-shrink-0 flex flex-col h-full bg-white dark:bg-[#0c0e14] border-l border-stone-200 dark:border-white/10">
      <div className="h-14 flex items-center justify-between px-4 border-b border-stone-200 dark:border-white/10 flex-shrink-0">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-indigo-600 dark:text-indigo-300" strokeWidth={2} />
          <h2 className="text-[14px] font-semibold text-stone-900 dark:text-white">Architect</h2>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={newChat} disabled={busy} title="New chat" aria-label="New chat" className="p-1.5 rounded-lg text-stone-500 hover:text-stone-900 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-white/10 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"><Plus className="w-4 h-4" /></button>
          <button onClick={closeArchitect} title="Close" aria-label="Close Architect" className="p-1.5 rounded-lg text-stone-500 hover:text-stone-900 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-white/10 cursor-pointer"><X className="w-4 h-4" /></button>
        </div>
      </div>

      <div
        ref={scroller}
        onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}
        className="flex-1 overflow-y-auto px-4 py-5 space-y-4"
      >
        {messages.length === 0 ? (
          <div className="pt-6">
            <span className="w-10 h-10 rounded-xl bg-indigo-50 dark:bg-indigo-500/15 flex items-center justify-center mb-4"><Sparkles className="w-5 h-5 text-indigo-600 dark:text-indigo-300" /></span>
            <p className="text-[14px] leading-relaxed text-stone-700 dark:text-stone-200">{GREETING}</p>
            <p className="mt-5 mb-2 text-[12px] font-medium text-stone-400">Try one of these</p>
            <div className="space-y-2">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => void send(s)} className="w-full text-left px-3.5 py-2.5 rounded-xl border border-stone-200 dark:border-white/10 text-[13px] text-stone-700 dark:text-stone-200 hover:bg-stone-50 dark:hover:bg-white/5 cursor-pointer">
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((m) => (
            <div key={m.id} className={m.role === 'user' ? 'flex justify-end' : ''}>
              {m.role === 'user' ? (
                <div className="max-w-[85%] rounded-2xl rounded-br-md bg-stone-900 text-white dark:bg-white dark:text-stone-900 px-3.5 py-2.5 text-[13px] leading-relaxed whitespace-pre-line">{m.text}</div>
              ) : (
                <div className="max-w-full space-y-3">
                  {m.run && (
                    <ThinkingBlock text={m.run.thinking} running={m.run.phase === 'thinking' && m.run.thinkingSecs === undefined} secs={m.run.thinkingSecs} />
                  )}
                  {m.text && <p className="text-[13.5px] leading-relaxed text-stone-800 dark:text-stone-100 whitespace-pre-line">{m.text}</p>}
                  {m.run && <Checklist steps={m.run.steps} />}

                  {m.run?.end && m.run.end.summary && (
                    <div className="space-y-2.5">
                      <p className={`text-[13.5px] leading-relaxed whitespace-pre-line ${m.run.end.ok ? 'text-stone-800 dark:text-stone-100' : 'text-rose-700 dark:text-rose-300'}`}>{m.run.end.summary}</p>
                      {!!m.run.end.notes?.length && (
                        <ul className="list-disc pl-4 space-y-0.5 text-[12px] text-stone-500 dark:text-stone-400">{m.run.end.notes.map((n) => <li key={n}>{n}</li>)}</ul>
                      )}
                      <div className="flex flex-wrap gap-2">
                        {m.run.end.agent_id && (
                          <a href={`/workflow?id=${m.run.end.agent_id}`} className={`${BTN} inline-flex items-center gap-1.5`}>Open in the builder <ArrowUpRight className="w-3.5 h-3.5" aria-hidden="true" /></a>
                        )}
                        {m.run.end.agent_id && <button onClick={() => setSelectedTab('agents' as TabId)} title="Make it live or take it offline there" className={BTN_OUTLINE}>Open Agents</button>}
                        {!!m.run.end.documents?.length && <button onClick={() => setSelectedTab('knowledge' as TabId)} className={BTN_OUTLINE}>Open Knowledge</button>}
                        {!m.run.end.ok && m.run.end.retry && <button onClick={() => retry(m.id)} disabled={busy} className={BTN}>Try again</button>}
                        {!m.run.end.ok && m.run.end.fallback && m.plain?.state !== 'created' && (
                          <button onClick={() => void createPlain(m.id, m.run!.end!.fallback!)} disabled={m.plain?.state === 'creating'} className={BTN_QUIET}>
                            {m.plain?.state === 'creating' ? 'Creating…' : 'Create one simple agent from your description instead'}
                          </button>
                        )}
                      </div>
                      {m.plain?.state === 'created' && (
                        <p className="text-[12.5px] text-emerald-700 dark:text-emerald-400">
                          Created, without desks or documents. It is a draft. <a className="underline" href={`/workflow?id=${m.plain.agentId}`}>Open it in the builder</a>.
                        </p>
                      )}
                      {m.plain?.state === 'failed' && <p role="alert" className="text-[12px] text-rose-600 dark:text-rose-400">{m.plain.error}</p>}
                    </div>
                  )}
                  {m.run?.error && <p role="alert" className="text-[13px] leading-relaxed text-rose-700 dark:text-rose-300">{m.run.error}</p>}
                  {m.run?.phase === 'stopped' && <p className="text-[12.5px] text-stone-500 dark:text-stone-400">Stopped. Nothing after this point was done; what was finished before is kept.</p>}

                  {m.run?.action?.type === 'show_knowledge' && <KnowledgeList documents={m.run.action.documents.map((d) => ({ ...d }))} onOpenPage={() => setSelectedTab('knowledge' as TabId)} />}
                  {m.run?.action?.type === 'change_speaking' && (
                    <SpeakingCard
                      proposal={m.run.action}
                      outcome={m.outcome}
                      onApply={() => void applySpeaking(m.id, (m.run!.action as SpeakingProposal))}
                      onDismiss={() => patch(m.id, (x) => ({ ...x, outcome: { state: 'dismissed' } }))}
                    />
                  )}
                  {m.run?.action?.type === 'open_page' && (
                    <button
                      onClick={() => setSelectedTab((m.run!.action as { tab: string }).tab as TabId)}
                      className={BTN_OUTLINE}
                    >
                      Open {(m.run.action as { label: string }).label} <ArrowUpRight className="w-3.5 h-3.5" aria-hidden="true" />
                    </button>
                  )}
                </div>
              )}
            </div>
          ))
        )}
      </div>
      <div className="sr-only" role="status" aria-live="polite">{announcement}</div>

      <form
        onSubmit={(e) => { e.preventDefault(); if (!busy) void send(draft); }}
        className="p-3 border-t border-stone-200 dark:border-white/10 flex-shrink-0"
      >
        <div className="rounded-2xl border border-stone-300 dark:border-white/15 bg-white dark:bg-white/5 focus-within:ring-2 focus-within:ring-indigo-500 px-3 py-2">
          <textarea
            ref={input}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!busy) void send(draft); } }}
            rows={2}
            maxLength={2000}
            placeholder="Describe the agent or document you want, or ask a question"
            aria-label="Message Architect"
            className="w-full resize-none bg-transparent text-[13.5px] text-stone-900 dark:text-white placeholder:text-stone-400 focus:outline-none"
          />
          <div className="flex items-center justify-between">
            <p className="text-[11px] text-stone-400">{busy ? (saving ? 'Saving. This cannot be stopped.' : 'Working. You can stop me.') : ''}</p>
            {busy ? (
              <button
                type="button"
                onClick={stop}
                disabled={saving}
                aria-label="Stop"
                title={saving ? 'Saving cannot be stopped' : 'Stop'}
                className="w-8 h-8 rounded-full bg-stone-900 dark:bg-white text-white dark:text-stone-900 flex items-center justify-center disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
              >
                <Square className="w-3.5 h-3.5" fill="currentColor" />
              </button>
            ) : (
              <button
                type="submit"
                disabled={!draft.trim()}
                aria-label="Send"
                className="w-8 h-8 rounded-full bg-stone-900 dark:bg-white text-white dark:text-stone-900 flex items-center justify-center disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
              >
                <ArrowUp className="w-4 h-4" strokeWidth={2.25} />
              </button>
            )}
          </div>
        </div>
      </form>
    </aside>
  );
}

