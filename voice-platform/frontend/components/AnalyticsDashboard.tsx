'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { BarChart3, RefreshCw } from 'lucide-react';
import { CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { fetchAnalyticsSummary, fetchAnalyticsTrends } from '@/lib/api';
import type { AnalyticsSummary } from '@/lib/api';
import { useDashboardStore } from '@/lib/store';
import { useThemeStore } from '@/lib/themeStore';
import { Button, Card, EmptyState, Page, Skeleton, StatCard } from '@/components/ui/kit';

// Coloured by what the word means, not by position: the server returns the groups in no fixed order.
const SENTIMENT_COLORS: Record<string, string> = { positive: '#10b981', neutral: '#a8a29e', negative: '#f43f5e' };
const SPARE_COLORS = ['#6366f1', '#8b5cf6', '#f59e0b', '#0ea5e9'];
const SENTIMENT_ORDER = ['positive', 'neutral', 'negative'];
const RANGES = [7, 30, 90];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

interface Trends {
  volume: { date: string; calls: number }[];
  sentiment: { name: string; value: number }[];
  intents: { name: string; count: number }[];
}

function minutes(seconds?: number | null) {
  if (!seconds) return '0:00';
  const s = Math.round(seconds);   // round the whole figure first, or 119.6 s reads as 1:60
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** The chart days arrive as "10/08" (month first). Show "8 Oct" so nobody reads it as 10 August. */
function dayLabel(md: string) {
  const m = /^(\d{1,2})[/-](\d{1,2})$/.exec(md);
  const month = m ? MONTHS[Number(m[1]) - 1] : undefined;
  return m && month ? `${Number(m[2])} ${month}` : md;
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

/** Tidy the topic list: merge spellings of one topic, start each with a capital, and set aside "unknown", which the agent
 *  saves when it never worked out what the call was about (it would otherwise top the list as a topic of its own). */
function tidyTopics(intents: Trends['intents']) {
  const merged = new Map<string, { name: string; count: number }>();
  let unclear = 0;
  for (const i of intents) {
    const name = (i.name ?? '').replace(/_/g, ' ').trim();   // ticket topics arrive as "network_printer"
    if (!name || ['unknown', 'none'].includes(name.toLowerCase())) { unclear += i.count; continue; }
    const hit = merged.get(name.toLowerCase());
    if (hit) hit.count += i.count;
    else merged.set(name.toLowerCase(), { name: name.charAt(0).toUpperCase() + name.slice(1), count: i.count });
  }
  return { topics: [...merged.values()].sort((a, b) => b.count - a.count), unclear };
}

export function AnalyticsDashboard() {
  const { refreshCounter } = useDashboardStore();
  const dark = useThemeStore((s) => s.theme) === 'dark';
  const [days, setDays] = useState(30);
  const [trends, setTrends] = useState<Trends | null>(null);
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const latest = useRef(0);

  const load = useCallback(async () => {
    const mine = ++latest.current;   // the 90-day figures are slow: an answer for an earlier range must not overwrite a newer one
    setLoading(true);
    setFailed(false);
    try {
      const [t, s] = await Promise.all([fetchAnalyticsTrends(days), fetchAnalyticsSummary(days)]);
      if (mine !== latest.current) return;
      const sentiment = [...(t.sentiment ?? [])].sort((a, b) => (SENTIMENT_ORDER.indexOf(a.name.toLowerCase()) + 1 || 99) - (SENTIMENT_ORDER.indexOf(b.name.toLowerCase()) + 1 || 99));
      setTrends({ volume: (t.volume ?? []).map((v) => ({ ...v, date: dayLabel(v.date) })), sentiment, intents: t.intents ?? [] });
      setSummary(s);
    } catch {
      if (mine === latest.current) setFailed(true);
    } finally {
      if (mine === latest.current) setLoading(false);
    }
  }, [days]);

  useEffect(() => { void load(); }, [load, refreshCounter]);

  const grid = dark ? '#262a36' : '#e7e5e4';
  const axis = dark ? '#8b90a0' : '#78716c';
  const tooltip = { backgroundColor: dark ? '#12141c' : '#ffffff', border: `1px solid ${dark ? '#262a36' : '#e7e5e4'}`, borderRadius: 8, fontSize: 12, color: dark ? '#f5f5f4' : '#1c1917' };
  const { handed, resolved: resolvedCalls } = summary ? handedOver(summary) : { handed: 0, resolved: 0 };
  const resolved = summary && summary.total_calls > 0 ? percent(resolvedCalls, summary.total_calls) : null;
  const { topics, unclear } = tidyTopics(trends?.intents ?? []);
  const maxIntent = Math.max(1, ...topics.map((i) => i.count));
  const noCalls = !loading && !!summary && summary.total_calls === 0;

  return (
    <Page
      title="Analytics"
      subtitle={`How calls went over the last ${days} days.`}
      actions={
        <>
          <div role="group" aria-label="Time range" className="inline-flex rounded-lg border border-stone-300 dark:border-white/15 overflow-hidden">
            {RANGES.map((r) => (
              <button
                key={r}
                onClick={() => setDays(r)}
                aria-pressed={days === r}
                className={`h-9 px-3 text-[13px] cursor-pointer ${days === r ? 'bg-stone-900 text-white dark:bg-white dark:text-stone-900' : 'bg-white dark:bg-transparent text-stone-600 dark:text-stone-300 hover:bg-stone-50 dark:hover:bg-white/5'}`}
              >
                {r}d
              </button>
            ))}
          </div>
          <Button onClick={load} disabled={loading} aria-label="Refresh"><RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /></Button>
        </>
      }
    >
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Calls" value={summary?.total_calls ?? 0} loading={loading && !summary} />
        <StatCard label="Resolved by the agent" value={resolved === null ? '—' : `${resolved}%`} loading={loading && !summary} />
        <StatCard label="Handed to a person" value={handed} loading={loading && !summary} />
        <StatCard label="Average call length" value={minutes(summary?.average_duration_seconds)} hint="minutes:seconds" loading={loading && !summary} />
      </div>

      {failed && (
        <Card><EmptyState icon={<BarChart3 className="w-5 h-5" />} title="Could not load analytics" text="Check your connection and try again." action={<Button onClick={load}>Try again</Button>} /></Card>
      )}

      {!failed && noCalls && (
        <Card><EmptyState icon={<BarChart3 className="w-5 h-5" strokeWidth={1.75} />} title="No calls in this period" text="Charts appear here once calls come in. Try the agent simulator to create a test call." /></Card>
      )}

      {!failed && !noCalls && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <Card title="Call volume" className="lg:col-span-2">
              <div className="h-64">
                {!trends ? <Skeleton className="h-full" /> : (
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={trends.volume} margin={{ left: -16, right: 8, top: 8 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={grid} vertical={false} />
                      <XAxis dataKey="date" stroke={axis} fontSize={12} tickLine={false} axisLine={false} minTickGap={24} />
                      <YAxis stroke={axis} fontSize={12} tickLine={false} axisLine={false} allowDecimals={false} />
                      <Tooltip contentStyle={tooltip} />
                      <Line type="monotone" dataKey="calls" name="Calls" stroke="#6366f1" strokeWidth={2.5} dot={false} activeDot={{ r: 5 }} />
                    </LineChart>
                  </ResponsiveContainer>
                )}
              </div>
            </Card>

            <Card title="Caller sentiment">
              <div className="h-64">
                {!trends ? <Skeleton className="h-full" /> : trends.sentiment.length > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={trends.sentiment} cx="50%" cy="45%" innerRadius={52} outerRadius={76} paddingAngle={4} dataKey="value" stroke="none">
                        {trends.sentiment.map((s, i) => <Cell key={`${s.name}-${i}`}fill={SENTIMENT_COLORS[s.name.toLowerCase()] ?? SPARE_COLORS[i % SPARE_COLORS.length]} />)}
                      </Pie>
                      <Tooltip contentStyle={tooltip} />
                      <Legend wrapperStyle={{ fontSize: 12, color: axis }} />
                    </PieChart>
                  </ResponsiveContainer>
                ) : <EmptyState title="No sentiment data yet" />}
              </div>
            </Card>
          </div>

          <Card title="What callers ask about">
            {!trends ? <Skeleton className="h-40" /> : topics.length === 0 ? (
              <EmptyState title="No topics recorded yet" text="Topics appear as the agent understands what each caller wants." />
            ) : (
              <>
                <ul className="space-y-3">
                  {topics.map((i) => (
                    <li key={i.name}>
                      <div className="flex items-center justify-between gap-3 text-[13px]">
                        <span className="text-stone-700 dark:text-stone-200 truncate" title={i.name}>{i.name}</span>
                        <span className="tabular-nums text-stone-500 dark:text-stone-400">{i.count}</span>
                      </div>
                      <div className="mt-1.5 h-1.5 rounded-full bg-stone-100 dark:bg-white/10 overflow-hidden">
                        <div className="h-full rounded-full bg-indigo-500" style={{ width: `${Math.max(4, (i.count / maxIntent) * 100)}%` }} />
                      </div>
                    </li>
                  ))}
                </ul>
                {unclear > 0 && <p className="mt-4 text-[12px] text-stone-500 dark:text-stone-400">The agent could not tell what {unclear} {unclear === 1 ? 'call was' : 'calls were'} about.</p>}
              </>
            )}
          </Card>
        </>
      )}
    </Page>
  );
}
