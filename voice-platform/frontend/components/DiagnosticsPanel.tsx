'use client';

import React, { useEffect, useState } from 'react';
import { fetchDiagnosticsOverview } from '@/lib/api';
import type { DiagnosticsOverview, DiagnosticCall } from '@/lib/api';
import { AlertTriangle, CheckCircle2, Radio, ScrollText, Sparkles, Ticket, RefreshCw } from 'lucide-react';
import { useDashboardStore } from '@/lib/store';

export function DiagnosticsPanel() {
  const { refreshCounter, selectCall, setSelectedTab } = useDashboardStore();
  const [data, setData] = useState<DiagnosticsOverview | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadDiagnostics();
  }, [refreshCounter]);

  async function loadDiagnostics() {
    setLoading(true);
    try {
      const result = await fetchDiagnosticsOverview(15);
      setData(result);
    } catch (err) {
      console.error('Failed to load diagnostics:', err);
    } finally {
      setLoading(false);
    }
  }

  function openCall(callId: number) {
    selectCall(callId);
    setSelectedTab('live');
  }

  return (
    <div className="flex-1 flex flex-col bg-[#fbfbfa] dark:bg-[#08090e] p-6 overflow-auto transition-colors duration-300">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-bold text-stone-900 dark:text-white">System Diagnostics & Event Traces</h2>
          <p className="text-xs text-stone-500 dark:text-stone-400 mt-0.5">Trace live SIP ingest, transcript save, AI summary, and ticket creation</p>
        </div>
        <button
          onClick={loadDiagnostics}
          disabled={loading}
          className="flex items-center gap-2 px-3.5 py-2 rounded-xl bg-stone-900 dark:bg-white text-white dark:text-stone-900 hover:bg-stone-800 dark:hover:bg-stone-100 text-xs font-semibold transition-colors disabled:opacity-50 shadow-xs cursor-pointer"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          {loading ? 'Running Diagnostics...' : 'Run Diagnostics'}
        </button>
      </div>

      {data && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          <SummaryCard label="Calls Checked" value={data.summary.checked_calls} tone="blue" />
          <SummaryCard label="Calls With Issues" value={data.summary.calls_with_issues} tone="amber" />
          <SummaryCard label="Open Issues" value={data.summary.open_issues} tone="rose" />
          <SummaryCard label="Recent Events" value={data.summary.recent_event_count} tone="emerald" />
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[2fr,1fr] gap-6">
        <div className="rounded-2xl border border-stone-200/90 dark:border-white/10 bg-white dark:bg-[#0d0f17] overflow-hidden shadow-xs">
          <div className="px-4 py-3 border-b border-stone-200/80 dark:border-white/10 bg-stone-50/60 dark:bg-stone-900/60">
            <h3 className="text-xs font-bold uppercase tracking-wider text-stone-800 dark:text-stone-200">Recent Call Ingestion Pipeline</h3>
          </div>
          <div className="divide-y divide-stone-100 dark:divide-white/5">
            {data?.calls.map((item) => (
              <DiagnosticCallRow key={item.call.id} item={item} onOpen={openCall} />
            ))}
            {!data?.calls?.length && (
              <div className="px-4 py-10 text-xs text-stone-400 text-center">
                {loading ? 'Running diagnostics pipeline...' : 'No diagnostics data yet'}
              </div>
            )}
          </div>
        </div>

        <div className="rounded-2xl border border-stone-200/90 dark:border-white/10 bg-white dark:bg-[#0d0f17] overflow-hidden shadow-xs">
          <div className="px-4 py-3 border-b border-stone-200/80 dark:border-white/10 bg-stone-50/60 dark:bg-stone-900/60">
            <h3 className="text-xs font-bold uppercase tracking-wider text-stone-800 dark:text-stone-200">Telemetry Events Stream</h3>
          </div>
          <div className="max-h-[70vh] overflow-auto divide-y divide-stone-100 dark:divide-white/5">
            {data?.recent_events.map((event) => (
              <div key={event.id} className="px-4 py-3">
                <p className="text-xs font-medium text-stone-800 dark:text-stone-200">{event.event_type}</p>
                <p className="text-[10px] text-stone-400 mt-1">
                  call #{event.call_id ?? '—'} · {formatTime(event.created_at)}
                </p>
              </div>
            ))}
            {!data?.recent_events?.length && (
              <div className="px-4 py-10 text-xs text-stone-400 text-center">No recent events</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function DiagnosticCallRow({ item, onOpen }: { item: DiagnosticCall; onOpen: (callId: number) => void }) {
  const call = item.call;
  const issueTone = item.stages.issues.length > 0;

  return (
    <button
      onClick={() => onOpen(call.id)}
      className="w-full text-left px-4 py-4 hover:bg-violet-50 transition-colors"
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-slate-800 dark:text-white">{call.caller_number || 'Unknown caller'}</p>
          <p className="text-xs text-slate-400 mt-1">
            {formatTime(call.started_at)} · {call.status} · {call.call_sid || call.conversation_id || 'no id'}
          </p>
        </div>
        <span className={`text-[11px] px-2 py-1 rounded-full border ${
          issueTone
            ? 'bg-amber-50 text-amber-600 border-amber-200'
            : 'bg-emerald-50 text-emerald-600 border-emerald-200'
        }`}>
          {issueTone ? `${item.stages.issues.length} issue${item.stages.issues.length > 1 ? 's' : ''}` : 'Healthy'}
        </span>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-2 mt-4">
        <StageBadge label="Live" ok={item.stages.live_received} icon={<Radio className="w-3 h-3" />} />
        <StageBadge label={`Transcript ${item.transcript_count}`} ok={item.stages.transcript_saved} icon={<ScrollText className="w-3 h-3" />} />
        <StageBadge label="AI Summary" ok={item.stages.ai_summary_created} icon={<Sparkles className="w-3 h-3" />} />
        <StageBadge label="Ticket" ok={item.stages.ticket_created} icon={<Ticket className="w-3 h-3" />} />
        <StageBadge label="Escalation" ok={item.stages.escalation_detected} icon={<AlertTriangle className="w-3 h-3" />} neutral />
      </div>

      {item.stages.issues.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {item.stages.issues.map((issue) => (
            <span
              key={issue}
              className="text-[11px] px-2 py-1 rounded-full bg-rose-50 text-rose-600 border border-rose-200"
            >
              {issue}
            </span>
          ))}
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {item.events.map((event) => (
          <span
            key={event.id}
            className="text-[10px] px-2 py-1 rounded-lg bg-slate-100 dark:bg-stone-800 text-slate-500 dark:text-stone-400 border border-slate-200 dark:border-white/10"
          >
            {event.event_type}
          </span>
        ))}
      </div>
    </button>
  );
}

function SummaryCard({ label, value, tone }: { label: string; value: number; tone: 'blue' | 'amber' | 'rose' | 'emerald' }) {
  const toneMap = {
    blue: 'border-blue-200 bg-blue-50 text-blue-700',
    amber: 'border-amber-200 bg-amber-50 text-amber-700',
    rose: 'border-rose-200 bg-rose-50 text-rose-700',
    emerald: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  };
  const valueMap = {
    blue: 'text-blue-800',
    amber: 'text-amber-800',
    rose: 'text-rose-800',
    emerald: 'text-emerald-800',
  };

  return (
    <div className={`p-4 rounded-xl border shadow-sm ${toneMap[tone]}`}>
      <p className={`text-2xl font-bold ${valueMap[tone]}`}>{value}</p>
      <p className="text-xs mt-1 opacity-70">{label}</p>
    </div>
  );
}

function StageBadge({
  label,
  ok,
  icon,
  neutral = false,
}: {
  label: string;
  ok: boolean;
  icon: React.ReactNode;
  neutral?: boolean;
}) {
  const className = neutral
    ? ok
      ? 'bg-amber-50 text-amber-600 border-amber-200'
      : 'bg-slate-100 text-slate-400 border-slate-200'
    : ok
      ? 'bg-emerald-50 text-emerald-600 border-emerald-200'
      : 'bg-slate-100 text-slate-400 border-slate-200';

  return (
    <div className={`flex items-center gap-1.5 px-2 py-1.5 rounded-lg border text-xs ${className}`}>
      {ok ? <CheckCircle2 className="w-3 h-3" /> : icon}
      <span>{label}</span>
    </div>
  );
}

function formatTime(iso: string | null) {
  if (!iso) return 'No timestamp';
  const utcIso = iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z';
  return new Date(utcIso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}
