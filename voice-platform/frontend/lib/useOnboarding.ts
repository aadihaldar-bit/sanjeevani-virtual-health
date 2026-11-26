'use client';

import { useEffect, useMemo, useState } from 'react';
import { authFetch } from '@/lib/authFetch';
import { getApiBase } from '@/lib/api';
import { useAgents } from '@/lib/useAgents';
import { useWorkspace } from '@/lib/useMe';
import { useShellStore } from '@/lib/shellStore';
import type { TabId } from '@/lib/store';

export interface Step {
  id: string;
  title: string;
  why: string;
  done: boolean;
  /** what the button does */
  cta: string;
  go: 'architect' | 'new-agent' | TabId;
}

const SIM_KEY = 'stl_tried_simulator';
const DISMISS_KEY = 'stl_onboarding_dismissed';
const WELCOME_KEY = 'stl_welcomed_v1';

const read = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };
const write = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* not remembered */ } };

export const onboardingFlags = {
  triedSimulator: () => write(SIM_KEY, '1'),
  welcomed: () => read(WELCOME_KEY) === '1',
  markWelcomed: () => write(WELCOME_KEY, '1'),
  dismissed: () => read(DISMISS_KEY) === '1',
  dismiss: () => write(DISMISS_KEY, '1'),
  restore: () => { try { localStorage.removeItem(DISMISS_KEY); } catch { /* ignore */ } },
};

async function json<T>(path: string): Promise<T | null> {
  try {
    const res = await authFetch(`${getApiBase()}${path}`);
    return res.ok ? ((await res.json()) as T) : null;
  } catch { return null; }
}

/**
 * How far a company has got, worked out from what it really has: agents, documents, a number, teammates, a live agent.
 * Only the steps this person can actually do are listed. STL's own staff are not onboarded.
 */
export function useOnboarding() {
  const { me, isPlatform, canManage, workspaceName } = useWorkspace();
  const { agents, loading: agentsLoading } = useAgents();
  const version = useShellStore((s) => s.agentsVersion);
  const [docs, setDocs] = useState<number | null>(null);
  const [numbers, setNumbers] = useState<{ total: number; assigned: number } | null>(null);
  const [members, setMembers] = useState<number | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => { setDismissed(onboardingFlags.dismissed()); }, []);

  useEffect(() => {
    if (!me || isPlatform) return;
    let cancelled = false;
    (async () => {
      const d = await json<{ total: number }>('/api/knowledge/documents');
      if (!cancelled) setDocs(d?.total ?? 0);
      if (me.capabilities?.manage_numbers) {
        const n = await json<{ numbers: { status: string }[] }>('/api/numbers');
        if (!cancelled) setNumbers({ total: n?.numbers.length ?? 0, assigned: (n?.numbers ?? []).filter((x) => x.status === 'assigned').length });
      }
      if (me.capabilities?.manage_members && me.tenant_id !== null) {
        const m = await json<{ members: unknown[] }>(`/api/tenants/${me.tenant_id}/members`);
        if (!cancelled) setMembers(m?.members.length ?? 1);
      }
    })();
    return () => { cancelled = true; };
  }, [me, isPlatform, version, tick]);

  const steps = useMemo<Step[]>(() => {
    if (!me || isPlatform || !canManage) return [];
    const list: Step[] = [
      { id: 'agent', title: 'Create your first agent', why: 'An agent is the voice that answers your calls. Start from a template, or describe it to Architect.', done: agents.length > 0, cta: 'Build with Architect', go: 'architect' },
      { id: 'knowledge', title: 'Add what your agent should know', why: 'Upload your FAQs, policies or manuals so callers get answers from your own material.', done: (docs ?? 0) > 0, cta: 'Add documents', go: 'knowledge' },
      { id: 'test', title: 'Try it in the simulator', why: 'Talk to your agent before any real caller does, and fix anything that sounds off.', done: read(SIM_KEY) === '1', cta: 'Open simulator', go: 'simulator' },
    ];
    if (me.capabilities?.manage_numbers) {
      list.push({ id: 'number', title: 'Connect a phone number', why: 'Numbers are allocated to your organization. Assign one to your agent so calls reach it.', done: (numbers?.assigned ?? 0) > 0, cta: 'Open phone numbers', go: 'admin-numbers' });
    }
    if (me.capabilities?.manage_members) {
      list.push({ id: 'team', title: 'Invite your team', why: 'Add colleagues and choose what each of them can do.', done: (members ?? 1) > 1, cta: 'Invite people', go: 'admin' });
    }
    list.push({ id: 'live', title: 'Make your agent live', why: 'Only one agent answers the phone at a time. Make yours live when you are happy with it.', done: agents.some((a) => a.is_active), cta: 'Go to agents', go: 'agents' });
    return list;
  }, [me, isPlatform, canManage, agents, docs, numbers, members, tick]);   // eslint-disable-line react-hooks/exhaustive-deps

  const doneCount = steps.filter((s) => s.done).length;
  const next = steps.find((s) => !s.done) || null;
  return {
    steps, doneCount, total: steps.length, next,
    complete: steps.length > 0 && doneCount === steps.length,
    applicable: !!me && !isPlatform && canManage,
    loading: agentsLoading || docs === null,
    dismissed,
    dismiss: () => { onboardingFlags.dismiss(); setDismissed(true); },
    restore: () => { onboardingFlags.restore(); setDismissed(false); },
    refresh: () => setTick((t) => t + 1),
    workspaceName,
  };
}
