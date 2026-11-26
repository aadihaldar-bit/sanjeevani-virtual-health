'use client';

import React, { useState } from 'react';

/**
 * All figures below are a snapshot of real STL VoiceBot production telemetry
 * pulled from the voice_agent database on 2026-10-05. No values are invented.
 */
const SNAPSHOT_DATE = 'Oct 5, 2026';
const TOTAL_CALLS = 121;
const TELEPHONY_CALLS = 107;
const SIM_CALLS = 14;
const ESCALATIONS = 3;
const RESOLVED = TOTAL_CALLS - ESCALATIONS;
const RESOLUTION_PCT = ((RESOLVED / TOTAL_CALLS) * 100).toFixed(1);
const TICKETS = 108;
const TURNS = 492;
const AVG_HANDLE = 98; // seconds
const MEDIAN_HANDLE = 50; // seconds

const CALLS_PER_DAY: { d: string; n: number }[] = [
  { d: '08-26', n: 3 }, { d: '08-27', n: 27 }, { d: '08-28', n: 8 }, { d: '08-31', n: 6 },
  { d: '09-01', n: 6 }, { d: '09-02', n: 2 }, { d: '09-03', n: 4 }, { d: '09-04', n: 12 },
  { d: '09-05', n: 6 }, { d: '09-06', n: 2 }, { d: '09-08', n: 2 }, { d: '09-09', n: 1 },
  { d: '09-10', n: 5 }, { d: '09-25', n: 8 }, { d: '09-28', n: 4 }, { d: '10-01', n: 15 },
  { d: '10-02', n: 10 },
];

const TICKET_PRIORITY = [
  { label: 'P1 · Critical', n: 59, color: '#e11d48' },
  { label: 'P2 · High', n: 24, color: '#f59e0b' },
  { label: 'P3 · Medium', n: 25, color: '#6366f1' },
];

const LANGUAGES = ['English (en-IN)', 'Hindi (hi-IN)', 'Tamil (ta-IN)', 'Telugu (te-IN)'];

type TabId = 'reliability' | 'speed' | 'scale' | 'multilingual' | 'tickets';

const TABS: { id: TabId; label: string; practice: string; approach: string }[] = [
  {
    id: 'reliability',
    label: 'Reliability',
    practice:
      'A caller should reach a resolution, not a queue. Most issues can be handled end-to-end, with a human brought in only when the case genuinely needs one.',
    approach:
      `Across ${TOTAL_CALLS} production calls, ${RESOLVED} were resolved by the agent without a human transfer — only ${ESCALATIONS} escalations.`,
  },
  {
    id: 'speed',
    label: 'Speed',
    practice:
      'Fast, natural turns keep a conversation feeling human. Calls should close quickly without the caller feeling rushed.',
    approach:
      `Median handle time is about ${MEDIAN_HANDLE}s and the average is ${AVG_HANDLE}s across completed calls, including multi-step tool-using sessions.`,
  },
  {
    id: 'scale',
    label: 'Scale',
    practice:
      'Volume is spiky — campaigns, incidents, and outages all drive bursts. Capacity has to absorb the peaks.',
    approach:
      `The platform has handled ${TOTAL_CALLS} calls (${TELEPHONY_CALLS} telephony, ${SIM_CALLS} simulator) with daily peaks well above the baseline.`,
  },
  {
    id: 'multilingual',
    label: 'Multilingual',
    practice:
      'An agent that sounds local earns trust from the first word — across English and major Indian languages.',
    approach:
      'Speech recognition and synthesis are tuned for Indian English and regional languages, with voices selectable per workflow.',
  },
  {
    id: 'tickets',
    label: 'Tickets',
    practice:
      'Every call should leave a clean record — a prioritised ticket the service desk can act on, created automatically.',
    approach:
      `The agent has auto-created ${TICKETS} tickets from ${TURNS} logged conversation turns, prioritised P1–P3.`,
  },
];

/* ─── Small inline charts ─────────────────────────────────────────────────── */
function CallsTrend() {
  const max = Math.max(...CALLS_PER_DAY.map((d) => d.n));
  const W = 440, H = 180, pad = 28;
  const bw = (W - pad * 2) / CALLS_PER_DAY.length;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto">
      <line x1={pad} y1={H - pad} x2={W - pad} y2={H - pad} className="stroke-stone-200 dark:stroke-white/10" />
      {CALLS_PER_DAY.map((d, i) => {
        const h = ((H - pad * 2) * d.n) / max;
        const x = pad + i * bw + 2;
        return (
          <g key={d.d}>
            <rect x={x} y={H - pad - h} width={bw - 4} height={h} rx="2" className="fill-indigo-500/80 dark:fill-indigo-400/80" />
            {(i % 3 === 0 || i === CALLS_PER_DAY.length - 1) && (
              <text x={x + (bw - 4) / 2} y={H - pad + 12} textAnchor="middle" className="fill-stone-400 text-[8px] font-mono">
                {d.d}
              </text>
            )}
          </g>
        );
      })}
      <text x={pad} y={16} className="fill-stone-400 text-[9px] font-mono">calls / day · peak {max}</text>
    </svg>
  );
}

function ResolutionDonut() {
  const r = 52, c = 2 * Math.PI * r;
  const frac = RESOLVED / TOTAL_CALLS;
  return (
    <div className="flex items-center gap-6">
      <svg viewBox="0 0 140 140" className="w-36 h-36">
        <circle cx="70" cy="70" r={r} fill="none" strokeWidth="14" className="stroke-stone-200 dark:stroke-white/10" />
        <circle
          cx="70" cy="70" r={r} fill="none" strokeWidth="14" strokeLinecap="round"
          className="stroke-indigo-500"
          strokeDasharray={`${c * frac} ${c}`}
          transform="rotate(-90 70 70)"
        />
        <text x="70" y="66" textAnchor="middle" className="fill-stone-900 dark:fill-white text-[22px] font-extrabold">{RESOLUTION_PCT}%</text>
        <text x="70" y="84" textAnchor="middle" className="fill-stone-400 text-[9px] font-mono">autonomous</text>
      </svg>
      <div className="space-y-2 text-xs">
        <div className="flex items-center gap-2"><span className="w-2.5 h-2.5 rounded-sm bg-indigo-500" /> Resolved by agent · {RESOLVED}</div>
        <div className="flex items-center gap-2"><span className="w-2.5 h-2.5 rounded-sm bg-stone-300 dark:bg-white/20" /> Escalated to human · {ESCALATIONS}</div>
      </div>
    </div>
  );
}

function TicketBars() {
  const max = Math.max(...TICKET_PRIORITY.map((t) => t.n));
  return (
    <div className="space-y-3 w-full">
      {TICKET_PRIORITY.map((t) => (
        <div key={t.label}>
          <div className="flex justify-between text-[11px] mb-1 text-stone-600 dark:text-stone-300">
            <span>{t.label}</span><span className="font-mono">{t.n}</span>
          </div>
          <div className="h-3 rounded-full bg-stone-100 dark:bg-white/5 overflow-hidden">
            <div className="h-full rounded-full" style={{ width: `${(t.n / max) * 100}%`, background: t.color }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function SpeedPanel() {
  return (
    <div className="grid grid-cols-2 gap-4 w-full">
      <div className="p-5 rounded-2xl bg-stone-50 dark:bg-stone-900 border border-stone-200 dark:border-white/5 text-center">
        <p className="text-[10px] font-mono text-stone-400 uppercase">Median handle time</p>
        <p className="text-3xl font-extrabold text-indigo-600 dark:text-indigo-400 mt-1">~{MEDIAN_HANDLE}s</p>
      </div>
      <div className="p-5 rounded-2xl bg-stone-50 dark:bg-stone-900 border border-stone-200 dark:border-white/5 text-center">
        <p className="text-[10px] font-mono text-stone-400 uppercase">Average handle time</p>
        <p className="text-3xl font-extrabold text-stone-900 dark:text-white mt-1">~{AVG_HANDLE}s</p>
      </div>
      <div className="col-span-2 p-4 rounded-2xl bg-stone-50 dark:bg-stone-900 border border-stone-200 dark:border-white/5 text-xs text-stone-500 dark:text-stone-400">
        Includes multi-step sessions where the agent calls tools (ticket lookup, SOP retrieval) mid-conversation.
      </div>
    </div>
  );
}

function MultilingualPanel() {
  return (
    <div className="space-y-2 w-full">
      {LANGUAGES.map((l) => (
        <div key={l} className="flex items-center justify-between px-4 py-3 rounded-xl bg-stone-50 dark:bg-stone-900 border border-stone-200 dark:border-white/5 text-sm text-stone-800 dark:text-stone-200">
          <span>{l}</span>
          <span className="text-[10px] font-mono text-emerald-600 dark:text-emerald-400">live</span>
        </div>
      ))}
    </div>
  );
}

export function CapabilityTabs() {
  const [active, setActive] = useState<TabId>('reliability');
  const tab = TABS.find((t) => t.id === active) || TABS[0];

  const panel =
    active === 'reliability' ? <ResolutionDonut /> :
    active === 'speed' ? <SpeedPanel /> :
    active === 'scale' ? <CallsTrend /> :
    active === 'multilingual' ? <MultilingualPanel /> :
    <TicketBars />;

  return (
    <section className="w-full border-t border-stone-200/80 dark:border-white/5">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-16">
        {/* Tab bar */}
        <div className="flex flex-wrap gap-1 p-1 rounded-2xl border border-stone-200 dark:border-white/10 bg-stone-50 dark:bg-stone-900/60 mb-10">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setActive(t.id)}
              className={`flex-1 min-w-[110px] px-4 py-2.5 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
                active === t.id
                  ? 'bg-white dark:bg-stone-800 text-stone-900 dark:text-white shadow-sm'
                  : 'text-stone-500 dark:text-stone-400 hover:text-stone-900 dark:hover:text-white'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-10 items-center">
          {/* Left copy */}
          <div>
            <p className="text-xs font-bold uppercase tracking-wider text-stone-400 mb-3">In practice</p>
            <p className="text-sm text-stone-700 dark:text-stone-300 leading-relaxed">{tab.practice}</p>
            <div className="my-6 h-px bg-stone-200 dark:bg-white/10" />
            <p className="text-xs font-bold uppercase tracking-wider text-stone-400 mb-3">How STL does it</p>
            <p className="text-sm text-stone-700 dark:text-stone-300 leading-relaxed">{tab.approach}</p>
          </div>

          {/* Right real-data panel */}
          <div className="rounded-3xl border border-stone-200/80 dark:border-white/10 bg-stone-50/50 dark:bg-[#0d0f17] p-6 sm:p-8 min-h-[260px] flex flex-col">
            <div className="flex items-center justify-between mb-5">
              <span className="text-[11px] font-mono uppercase tracking-wider text-stone-400">Production telemetry</span>
              <span className="text-[10px] font-mono text-stone-400">snapshot · {SNAPSHOT_DATE}</span>
            </div>
            <div className="flex-1 flex items-center justify-center">{panel}</div>
          </div>
        </div>
      </div>
    </section>
  );
}
