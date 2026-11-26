'use client';

import React, { useEffect, useState } from 'react';
import { ArrowDownRight, ArrowUpRight, CalendarCheck, Clock, Hourglass, Minus, Smile, Ticket, Users } from 'lucide-react';
import { fetchKpis } from '@/lib/api';
import type { Kpis } from '@/lib/api';
import type { TabId } from '@/lib/store';
import { Skeleton } from '@/components/ui/kit';

/** undefined while loading, null when the figures could not be fetched. */
export function useKpis(days: number) {
  const [kpis, setKpis] = useState<Kpis | null | undefined>(undefined);
  useEffect(() => {
    let cancelled = false;
    setKpis(undefined);
    fetchKpis(days).then((k) => { if (!cancelled) setKpis(k); }).catch(() => { if (!cancelled) setKpis(null); });
    return () => { cancelled = true; };
  }, [days]);
  return kpis;
}

export interface Change {
  text: string;
  dir: 'up' | 'down' | 'flat';
  /** whether the move is good news, bad news or just a fact */
  tone: 'good' | 'bad' | 'flat';
}

/** How a figure moved since the stretch before it. `better` says which way is good; leave it out for plain facts like volume. */
export function change(current: number | null | undefined, previous: number | null | undefined,
  opts: { points?: boolean; better?: 'up' | 'down' } = {}): Change | null {
  if (current == null || previous == null) return null;
  let text: string;
  let diff: number;
  if (opts.points) {
    diff = Math.round((current - previous) * 10) / 10;
    text = diff === 0 ? 'No change' : `${diff > 0 ? '+' : '−'}${Math.abs(diff)} pts`;
  } else if (previous === 0) {
    if (current === 0) return null;
    return { text: 'New', dir: 'up', tone: 'flat' };
  } else {
    diff = Math.round(((current - previous) / previous) * 100);
    text = diff === 0 ? 'No change' : `${diff > 0 ? '+' : '−'}${Math.abs(diff)}%`;
  }
  const dir = diff === 0 ? 'flat' : diff > 0 ? 'up' : 'down';
  const tone = dir === 'flat' || !opts.better ? 'flat' : dir === opts.better ? 'good' : 'bad';
  return { text, dir, tone };
}

const TONE = {
  good: 'text-emerald-600 dark:text-emerald-400',
  bad: 'text-rose-600 dark:text-rose-400',
  flat: 'text-stone-500 dark:text-stone-400',
};

export function ChangeChip({ value, days }: { value: Change | null; days: number }) {
  if (!value) return null;
  const Icon = value.dir === 'up' ? ArrowUpRight : value.dir === 'down' ? ArrowDownRight : Minus;
  return (
    <span className={`inline-flex items-center gap-0.5 text-[12px] font-medium tabular-nums ${TONE[value.tone]}`} title={`Compared with the previous ${days} days`}>
      <Icon className="w-3.5 h-3.5" strokeWidth={2} aria-hidden /> {value.text}
    </span>
  );
}

/** The four headline tiles' movement, keyed like the tiles. */
export function headlineChanges(k: Kpis | null | undefined) {
  if (!k) return null;
  const { current: c, previous: p } = k;
  return {
    calls: change(c.conversations, p.conversations),
    resolved: change(c.resolved_rate_percent, p.resolved_rate_percent, { points: true, better: 'up' }),
    handed: change(c.handed_over, p.handed_over, { better: 'down' }),
    duration: change(c.average_duration_seconds, p.average_duration_seconds),
  };
}

export function talkTime(seconds: number) {
  if (seconds < 60) return `${Math.round(seconds)} s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  const hours = seconds / 3600;
  return `${hours >= 100 ? Math.round(hours) : (Math.round(hours * 10) / 10).toString()} h`;
}

const ZONE_NAMES: Record<string, string> = { 'Asia/Dubai': 'Gulf', 'Asia/Muscat': 'Oman', 'Asia/Riyadh': 'Saudi', 'Asia/Qatar': 'Qatar', 'Asia/Kuwait': 'Kuwait', 'Asia/Bahrain': 'Bahrain', 'Asia/Kolkata': 'India', UTC: 'UTC' };

function hourLabel(h: number) {
  return `${h % 12 || 12} ${h < 12 ? 'AM' : 'PM'}`;
}

interface Figure {
  key: string;
  icon: React.ReactNode;
  label: string;
  value: string;
  sub?: React.ReactNode;
  go?: TabId;
  /** words rather than a number: set smaller so a long agent name or time range still fits */
  words?: boolean;
}

function figuresOf(k: Kpis, days: number, showAppointments: boolean): Figure[] {
  const { current: c, previous: p } = k;
  const rated = c.rated;
  const peakHour = k.peak_hour ?? k.peak_hour_ist ?? null;
  const list: Figure[] = [
    {
      key: 'talk', icon: <Clock className="w-4 h-4" strokeWidth={1.75} />, label: 'Time on calls', value: talkTime(c.talk_seconds),
      sub: <ChangeChip value={change(c.talk_seconds, p.talk_seconds)} days={days} />,
    },
    {
      key: 'tickets', icon: <Ticket className="w-4 h-4" strokeWidth={1.75} />, label: 'Tickets raised', value: String(c.tickets), go: 'tickets',
      sub: <ChangeChip value={change(c.tickets, p.tickets)} days={days} />,
    },
  ];
  if (showAppointments) {
    list.push({
      key: 'appointments', icon: <CalendarCheck className="w-4 h-4" strokeWidth={1.75} />, label: 'Appointments booked', value: String(c.appointments), go: 'health',
      sub: <ChangeChip value={change(c.appointments, p.appointments)} days={days} />,
    });
  }
  list.push(
    {
      key: 'sentiment', icon: <Smile className="w-4 h-4" strokeWidth={1.75} />, label: 'Callers pleased',
      value: rated ? `${Math.round((c.positive / rated) * 100)}%` : '—',
      sub: rated ? (
        <span className="block w-full">
          <span className="flex h-1.5 rounded-full overflow-hidden bg-stone-100 dark:bg-white/10" role="img"
            aria-label={`${c.positive} positive, ${c.neutral} neutral, ${c.negative} negative`}>
            <span className="bg-emerald-500" style={{ width: `${(c.positive / rated) * 100}%` }} />
            <span className="bg-stone-300 dark:bg-stone-500" style={{ width: `${(c.neutral / rated) * 100}%` }} />
            <span className="bg-rose-500" style={{ width: `${(c.negative / rated) * 100}%` }} />
          </span>
          <span className="block mt-1 text-[12px] text-stone-500 dark:text-stone-400">{rated} of {c.conversations} conversations rated</span>
        </span>
      ) : <span className="text-[12px] text-stone-500 dark:text-stone-400">Not recorded yet</span>,
    },
    {
      key: 'hour', words: true, icon: <Hourglass className="w-4 h-4" strokeWidth={1.75} />, label: 'Busiest hour',
      value: peakHour === null ? '—' : `${hourLabel(peakHour)} – ${hourLabel((peakHour + 1) % 24)}`,
      sub: peakHour === null ? undefined : <span className="text-[12px] text-stone-500 dark:text-stone-400">{ZONE_NAMES[k.timezone || 'Asia/Kolkata'] || 'Local'} time · {k.peak_hour_calls} {k.peak_hour_calls === 1 ? 'call' : 'calls'}</span>,
    },
    {
      key: 'agent', words: true, icon: <Users className="w-4 h-4" strokeWidth={1.75} />, label: 'Busiest agent', value: k.top_agent?.name ?? '—', go: 'agents',
      sub: k.top_agent ? <span className="text-[12px] text-stone-500 dark:text-stone-400">{k.top_agent.calls} {k.top_agent.calls === 1 ? 'conversation' : 'conversations'}</span> : undefined,
    },
  );
  return list;
}

/**
 * A row of plain figures under the Home chart: time on calls, tickets, appointments (only where there are any), how
 * pleased callers sounded, the busiest hour and the busiest agent. Each one shows how it moved against the stretch before.
 */
export function KeyFigures({ kpis, days, showAppointments, onGo }: {
  kpis: Kpis | null | undefined;
  days: number;
  showAppointments: boolean;
  onGo: (tab: TabId) => void;
}) {
  if (kpis === null) return null;           // the older backend has no such endpoint; the rest of Home still works
  const appointmentsMatter = !!kpis && showAppointments && kpis.current.appointments + kpis.previous.appointments > 0;
  const figures = kpis ? figuresOf(kpis, days, appointmentsMatter) : null;

  return (
    <section aria-label="Key figures">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 mb-3">
        <h3 className="text-[13px] font-semibold text-stone-900 dark:text-white">Key figures</h3>
        <span className="text-[12px] text-stone-500 dark:text-stone-400">Last {days} days, against the {days} before</span>
      </div>
      <div className="grid gap-3 grid-cols-2 lg:grid-cols-[repeat(auto-fit,minmax(170px,1fr))]">
        {figures === null
          ? Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-[92px] rounded-xl" />)
          : figures.map((f) => {
            const body = (
              <>
                <span className="flex items-start gap-2 text-[13px] leading-5 text-stone-500 dark:text-stone-400"><span className="mt-0.5 flex-shrink-0">{f.icon}</span><span>{f.label}</span></span>
                <span className={`block mt-2 font-medium tabular-nums text-stone-900 dark:text-white [overflow-wrap:anywhere] ${f.words ? 'text-[17px] leading-6' : 'text-[22px] leading-7'}`}>{f.value}</span>
                {f.sub && <span className="block mt-1 min-h-[18px]">{f.sub}</span>}
              </>
            );
            const base = 'rounded-xl border border-stone-200 dark:border-white/10 bg-white dark:bg-[#0f121a] p-4 text-left min-w-0';
            return f.go
              ? <button key={f.key} onClick={() => onGo(f.go!)} className={`${base} cursor-pointer hover:border-stone-300 dark:hover:border-white/20 transition-colors`}>{body}</button>
              : <div key={f.key} className={base}>{body}</div>;
          })}
      </div>
      {kpis && kpis.current.test_calls > 0 && (
        <p className="mt-2 text-[12px] text-stone-500 dark:text-stone-400">
          These include {kpis.current.test_calls} test {kpis.current.test_calls === 1 ? 'call' : 'calls'} made in the simulator.
        </p>
      )}
    </section>
  );
}
