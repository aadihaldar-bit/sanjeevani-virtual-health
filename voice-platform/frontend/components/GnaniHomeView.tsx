'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { ArrowRight, BarChart3, Bot, ChevronRight, Phone, Plus, Sparkles, X } from 'lucide-react';
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { useDashboardStore } from '@/lib/store';
import { authFetch } from '@/lib/authFetch';
import { useAuth } from '@/lib/useAuth';
import { getApiBase, fetchAnalyticsSummary, fetchDailyAnalytics } from '@/lib/api';
import type { AnalyticsSummary, DailyData } from '@/lib/api';
import { useWorkspace } from '@/lib/useMe';
import { useShellStore } from '@/lib/shellStore';
import { useThemeStore } from '@/lib/themeStore';
import { useOnboarding } from '@/lib/useOnboarding';
import { useStepRunner } from '@/components/onboarding/GetStarted';
import { Badge, Button, Card, EmptyState, Page, Skeleton, timeAgo } from '@/components/ui/kit';
import { ChangeChip, KeyFigures, headlineChanges, useKpis } from '@/components/home/KeyFigures';
import { PatientCareFigures } from '@/components/sanjeevani/PatientCareFigures';
import { domainTabsForWorkflow } from '@/lib/workflowTabs';

interface AgentCard {
  id: number;
  name: string;
  is_active: boolean;
  voice_name: string;
  languages: string[];
  node_count: number;
}

interface RecentCall {
  id: number;
  caller_number?: string | null;
  status?: string | null;
  started_at?: string | null;
  intent?: string | null;
  is_escalated?: boolean | null;
  transferred_to_human?: boolean | null;
}

type MetricKey = 'calls' | 'resolved' | 'handed' | 'duration';
const RANGES = [7, 30, 90];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const CALL_STATUS: Record<string, string> = { active: 'In progress', completed: 'Completed', escalated: 'Handed over', failed: 'Failed' };

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
}

function minutes(seconds?: number | null) {
  if (!seconds) return '—';
  const s = Math.round(seconds);   // round the whole figure first, or 119.6 s reads as 1:60
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** "2026-10-08" -> "8 Oct". A day-first label, so nobody reads 08-10 as 10 August. */
function dayLabel(iso: string) {
  const m = /^\d{4}-(\d{2})-(\d{2})/.exec(iso);
  const month = m ? MONTHS[Number(m[1]) - 1] : undefined;
  return m && month ? `${Number(m[2])} ${month}` : iso;
}

/** The topic of a call, or null when there is none ("unknown" is what the agent saves when it never worked it out). */
function topicOf(intent?: string | null) {
  const t = (intent ?? '').replace(/_/g, ' ').trim();   // ticket topics arrive as "network_printer"
  return !t || ['unknown', 'none'].includes(t.toLowerCase()) ? null : t.charAt(0).toUpperCase() + t.slice(1);
}

/** Calls that went to a person. Live phone calls only set "transferred to human", the older webhook path sets both flags,
 *  so the higher of the two is the count; the rest of the completed calls were resolved by the agent. */
function handedOver(s: AnalyticsSummary) {
  const handed = Math.max(s.escalated_calls, s.transferred_calls ?? 0);
  return { handed, resolved: Math.max(0, s.ai_resolved_calls - Math.max(0, handed - s.escalated_calls)) };
}

/** Whole-number percent that never says 100% while something was left out. */
function percent(part: number, whole: number) {
  const p = Math.round((part / whole) * 100);
  return part < whole && p === 100 ? 99 : p;
}

async function getJson<T>(path: string): Promise<T | null> {
  try {
    const res = await authFetch(`${getApiBase()}${path}`);
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}

export function GnaniHomeView(_props: { onBackToLanding?: () => void }) {
  const { user } = useAuth();
  const { isPlatform, canManage, workspaceName } = useWorkspace();
  const { setSelectedTab } = useDashboardStore();
  const { openNewAgent } = useShellStore();
  const dark = useThemeStore((s) => s.theme) === 'dark';
  const onboarding = useOnboarding();
  const run = useStepRunner();

  const [days, setDays] = useState(30);
  const [metric, setMetric] = useState<MetricKey>('calls');
  const [agents, setAgents] = useState<AgentCard[] | null>(null);
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [daily, setDaily] = useState<DailyData[] | null>(null);
  const [calls, setCalls] = useState<RecentCall[] | null>(null);
  const agentsVersion = useShellStore((s) => s.agentsVersion);
  const kpis = useKpis(days);
  const changes = headlineChanges(kpis);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [a, c] = await Promise.all([getJson<AgentCard[]>('/api/agents/'), getJson<{ calls: RecentCall[] }>('/api/calls?limit=5')]);
      if (cancelled) return;
      setAgents(Array.isArray(a) ? a : []);
      setCalls(c?.calls ?? []);
    })();
    return () => { cancelled = true; };
  }, [agentsVersion]);

  useEffect(() => {
    let cancelled = false;
    setSummary(null); setDaily(null);
    (async () => {
      const [s, d] = await Promise.all([fetchAnalyticsSummary(days).catch(() => null), fetchDailyAnalytics(days).catch(() => null)]);
      if (cancelled) return;
      setSummary(s); setDaily(d?.data ?? []);
    })();
    return () => { cancelled = true; };
  }, [days]);

  const firstName = (user?.displayName || user?.email?.split('@')[0] || 'there').split(' ')[0];
  const { handed, resolved } = summary ? handedOver(summary) : { handed: 0, resolved: 0 };
  const resolvedPct = summary && summary.total_calls > 0 ? percent(resolved, summary.total_calls) : null;
  const tiles: { key: MetricKey; label: string; value: string }[] = [
    { key: 'calls', label: 'Conversations', value: summary ? String(summary.total_calls) : '—' },
    { key: 'resolved', label: 'Resolved by the agent', value: resolvedPct === null ? '—' : `${resolvedPct}%` },
    { key: 'handed', label: 'Handed to a person', value: summary ? String(handed) : '—' },
    { key: 'duration', label: 'Average duration', value: minutes(summary?.average_duration_seconds) },
  ];

  const series = useMemo(() => (daily ?? []).map((d) => ({
    date: dayLabel(d.date),
    value: metric === 'resolved' ? d.ai_resolved : metric === 'handed' ? d.escalated_calls : d.total_calls,
  })), [daily, metric]);
  const hasSeries = metric !== 'duration' && series.some((p) => p.value > 0);
  // The daily figures only know the older "escalated" flag, so handovers can show 0 on every day while the tile above counts some.
  const noChart = metric === 'duration'
    ? { title: 'Average duration is shown above', text: 'The chart tracks counts per day. Pick another metric to see a trend.' }
    : metric === 'handed' && summary && summary.total_calls > 0
      ? handed > 0
        ? { title: 'No day-by-day view of handovers yet', text: `${handed} ${handed === 1 ? 'conversation was' : 'conversations were'} handed to a person in this period. They are counted above, but not charted by day.` }
        : { title: 'Nothing was handed to a person', text: 'No conversation in this period was passed to a person.' }
      : { title: 'No data has been collected', text: 'Once calls come in, they appear here. Try an agent in the simulator to create a test call.' };
  const grid = dark ? '#262a36' : '#ececea';
  const axis = dark ? '#8b90a0' : '#78716c';
  const showBanner = onboarding.applicable && !onboarding.loading && !onboarding.complete && !onboarding.dismissed;

  return (
    <Page width="max-w-6xl">
      <div className="space-y-1">
        <span className="inline-flex items-center gap-2 h-8 px-3.5 rounded-full border border-stone-200 dark:border-white/10 bg-white dark:bg-[#0f121a] text-[13px] font-medium text-stone-900 dark:text-white mb-3">
          <span className={`w-2 h-2 rounded-full ${(summary?.active_calls ?? 0) > 0 ? 'bg-emerald-500 animate-pulse' : 'bg-stone-900 dark:bg-white'}`} />
          Active conversations: {summary?.active_calls ?? 0}
          {kpis && <span className="text-stone-500 dark:text-stone-400 font-normal">· Today: {kpis.calls_today}</span>}
        </span>
        <p className="text-[14px] text-stone-500 dark:text-stone-400">{isPlatform ? 'STL Digital' : workspaceName}</p>
        <h2 className="text-[34px] leading-tight font-medium tracking-tight text-stone-900 dark:text-white">{greeting()}, {firstName}</h2>
      </div>

      {showBanner && (
        <div className="rounded-xl border border-indigo-200 dark:border-indigo-400/30 bg-indigo-50/60 dark:bg-indigo-500/10 p-5">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="flex items-center gap-2 text-[14px] font-semibold text-indigo-900 dark:text-indigo-100"><Sparkles className="w-4 h-4" /> Get started: {onboarding.doneCount} of {onboarding.total} done</p>
              <p className="mt-1 text-[13px] text-indigo-900/75 dark:text-indigo-100/75">
                {onboarding.next ? <><b>Next:</b> {onboarding.next.title}. {onboarding.next.why}</> : 'Nearly there.'}
              </p>
            </div>
            <button onClick={onboarding.dismiss} aria-label="Hide this for now" title="Hide this for now" className="p-1 rounded-md text-indigo-900/50 dark:text-indigo-100/50 hover:text-indigo-900 dark:hover:text-white cursor-pointer"><X className="w-4 h-4" /></button>
          </div>
          <div className="mt-3 h-1.5 rounded-full bg-indigo-100 dark:bg-white/10 overflow-hidden"><div className="h-full rounded-full bg-indigo-600 transition-all duration-500" style={{ width: `${Math.round((onboarding.doneCount / Math.max(onboarding.total, 1)) * 100)}%` }} /></div>
          <div className="mt-4 flex flex-wrap gap-2">
            {onboarding.next && <Button variant="primary" onClick={() => run(onboarding.next!.go)}>{onboarding.next.cta} <ArrowRight className="w-4 h-4" /></Button>}
            <Button onClick={() => setSelectedTab('get-started')}>See all steps</Button>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div role="group" aria-label="Time range" className="inline-flex rounded-lg border border-stone-200 dark:border-white/10 overflow-hidden bg-white dark:bg-[#0f121a]">
          {RANGES.map((r) => (
            <button key={r} onClick={() => setDays(r)} aria-pressed={days === r}
              className={`h-8 px-3.5 text-[13px] cursor-pointer ${days === r ? 'bg-stone-900 text-white dark:bg-white dark:text-stone-900' : 'text-stone-600 dark:text-stone-300 hover:bg-stone-50 dark:hover:bg-white/5'}`}>
              Last {r} days
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          <Button onClick={() => setSelectedTab('simulator')}>Try an agent</Button>
          {canManage && <Button variant="primary" onClick={() => openNewAgent()}><Plus className="w-4 h-4" /> New agent</Button>}
        </div>
      </div>

      <section className="rounded-2xl border border-stone-200 dark:border-white/10 bg-white dark:bg-[#0f121a] overflow-hidden">
        <div role="tablist" aria-label="Metric" className="grid grid-cols-2 lg:grid-cols-4 bg-stone-50/70 dark:bg-white/[0.03] border-b border-stone-200 dark:border-white/10">
          {tiles.map((t) => {
            const on = metric === t.key;
            return (
              <button key={t.key} role="tab" aria-selected={on} onClick={() => setMetric(t.key)}
                className={`relative text-left px-5 py-4 cursor-pointer transition-colors ${on ? 'bg-white dark:bg-[#0f121a]' : 'hover:bg-stone-100/60 dark:hover:bg-white/5'}`}>
                <span className="block text-[13px] text-stone-500 dark:text-stone-400">{t.label}</span>
                {summary === null ? <Skeleton className="mt-2 h-7 w-14" /> : <span className="block mt-1 text-[26px] font-medium tabular-nums text-stone-900 dark:text-white">{t.value}</span>}
                <span className="block mt-0.5 min-h-[18px]"><ChangeChip value={changes?.[t.key] ?? null} days={days} /></span>
                {on && <span className="absolute inset-x-0 -bottom-px h-0.5 bg-stone-900 dark:bg-white" />}
              </button>
            );
          })}
        </div>
        <div className="h-72 px-4 py-5">
          {daily === null ? <Skeleton className="h-full rounded-lg" />
            : hasSeries ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={series} margin={{ left: -18, right: 8, top: 8 }}>
                  <defs><linearGradient id="homeFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#6366f1" stopOpacity={0.28} /><stop offset="100%" stopColor="#6366f1" stopOpacity={0} /></linearGradient></defs>
                  <CartesianGrid strokeDasharray="3 3" stroke={grid} vertical={false} />
                  <XAxis dataKey="date" stroke={axis} fontSize={12} tickLine={false} axisLine={false} minTickGap={28} />
                  <YAxis stroke={axis} fontSize={12} tickLine={false} axisLine={false} allowDecimals={false} />
                  <Tooltip contentStyle={{ backgroundColor: dark ? '#12141c' : '#fff', border: `1px solid ${grid}`, borderRadius: 8, fontSize: 12, color: dark ? '#f5f5f4' : '#1c1917' }} />
                  <Area type="monotone" dataKey="value" name={tiles.find((t) => t.key === metric)?.label} stroke="#6366f1" strokeWidth={2.25} fill="url(#homeFill)" />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <EmptyState icon={<BarChart3 className="w-6 h-6" strokeWidth={1.5} />} title={noChart.title} text={noChart.text} />
            )}
        </div>
      </section>

      <KeyFigures kpis={kpis} days={days} showAppointments={isPlatform} onGo={setSelectedTab} />

      {(agents ?? []).some((a) => domainTabsForWorkflow(a.name).includes('sanjeevani-followups')) && <PatientCareFigures onGo={setSelectedTab} />}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
        <Card title="Your agents" padded={false}
          action={(agents?.length ?? 0) > 0 ? <button onClick={() => setSelectedTab('agents')} className="text-[12px] text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer">View all</button> : undefined}>
          {agents === null ? <div className="p-5 pt-3 space-y-3"><Skeleton className="h-12" /><Skeleton className="h-12" /></div>
            : agents.length === 0 ? (
              <EmptyState icon={<Bot className="w-5 h-5" strokeWidth={1.75} />}
                title={isPlatform ? 'No agents yet' : `Welcome to ${workspaceName}`}
                text={!canManage ? 'No agents have been set up for your organization yet. Your administrator can create them.' : 'This is your own blank workspace. Build your first agent with Architect, or start from a ready-made workflow.'}
                action={canManage ? <Button variant="primary" onClick={() => openNewAgent()}><Plus className="w-4 h-4" /> Create an agent</Button> : undefined} />
            ) : (
              <ul className="divide-y divide-stone-100 dark:divide-white/5 mt-3">
                {agents.slice(0, 6).map((a) => (
                  <li key={a.id} className="flex items-center gap-3 px-5 py-3">
                    <span className="w-9 h-9 rounded-lg bg-stone-100 dark:bg-white/5 flex items-center justify-center text-stone-500 flex-shrink-0"><Bot className="w-4 h-4" strokeWidth={1.75} /></span>
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] font-medium text-stone-900 dark:text-white truncate" title={a.name}>{a.name}</p>
                      <p className="text-[12px] text-stone-500 dark:text-stone-400 truncate">{a.voice_name || 'Default voice'} · {a.node_count} steps</p>
                    </div>
                    {a.is_active ? <Badge tone="green">Live</Badge> : null}
                    <a href={`/workflow?id=${a.id}`} className="text-[12px] text-stone-500 hover:text-stone-900 dark:hover:text-white inline-flex items-center gap-1">Open <ArrowRight className="w-3 h-3" /></a>
                  </li>
                ))}
              </ul>
            )}
        </Card>

        <Card title="Recent calls" padded={false}
          action={(calls?.length ?? 0) > 0 ? <button onClick={() => setSelectedTab('completed')} className="text-[12px] text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer">View history</button> : undefined}>
          {calls === null ? <div className="p-5 pt-3 space-y-3"><Skeleton className="h-10" /><Skeleton className="h-10" /></div>
            : calls.length === 0 ? (
              <EmptyState icon={<Phone className="w-5 h-5" strokeWidth={1.75} />} title="No calls yet" text="Calls to your numbers, and test calls from the simulator, will show up here." />
            ) : (
              <ul className="divide-y divide-stone-100 dark:divide-white/5 mt-3">
                {calls.map((c) => {
                  const topic = topicOf(c.intent) || 'No topic recorded';
                  const toPerson = !!(c.is_escalated || c.transferred_to_human);
                  return (
                    <li key={c.id}>
                      <button onClick={() => setSelectedTab('completed')} className="w-full flex items-center gap-3 px-5 py-3 text-left hover:bg-stone-50 dark:hover:bg-white/5 cursor-pointer">
                        <div className="min-w-0 flex-1">
                          <p className="text-[13px] font-medium text-stone-900 dark:text-white truncate">{c.caller_number || 'Unknown caller'}</p>
                          <p className="text-[12px] text-stone-500 dark:text-stone-400 truncate" title={topic}>{topic}</p>
                        </div>
                        {toPerson ? <Badge tone="amber">Handed over</Badge>
                          : <Badge tone={c.status === 'active' ? 'green' : 'grey'}>{CALL_STATUS[c.status || 'completed'] ?? c.status}</Badge>}
                        <span className="text-[12px] text-stone-400 tabular-nums w-20 text-right">{timeAgo(c.started_at)}</span>
                        <ChevronRight className="w-4 h-4 text-stone-300" />
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
        </Card>
      </div>
    </Page>
  );
}
