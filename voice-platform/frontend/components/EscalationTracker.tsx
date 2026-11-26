'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { fetchEscalations, fetchCalls, type CallData } from '@/lib/api';
import { useDashboardStore } from '@/lib/store';
import { AlertTriangle, CheckCircle2, XCircle, Clock, ArrowUpRight } from 'lucide-react';

/** One line in the list: a row from the escalations table, or a call that was handed to a person without one. */
interface HandoverRow {
  key: string;
  call_id: number;
  /** requested / started / completed / failed, or "transferred" for a call that has no escalations row */
  status: string;
  reason: string | null;
  caller_number: string | null;
  transferred_to: string | null;
  agent: string | null;
  requested_at: string | null;
  completed_at: string | null;
}

const STATUS_LABEL: Record<string, string> = {
  requested: 'Requested',
  started: 'Started',
  completed: 'Completed',
  failed: 'Failed',
  transferred: 'Handed over',
};
const statusLabel = (status: string) => STATUS_LABEL[status] || (status ? status.charAt(0).toUpperCase() + status.slice(1) : 'Requested');

const CALL_PAGE = 200; // the most /api/calls returns in one go
const MAX_CALL_PAGES = 5;

/** Timestamps are stored in UTC without a zone marker; the console shows India time. */
function parseUtc(iso: string): Date {
  return new Date(/(Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : iso + 'Z');
}

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  const d = parseUtc(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

const sortKey = (iso: string | null) => (iso ? parseUtc(iso).getTime() || 0 : 0);

/**
 * Calls the agent handed to a person are flagged on the call itself (transferred_to_human), but only some paths also
 * write an escalations row, so the escalations table alone can read empty while calls were really handed over.
 * There is no server filter for this, so walk the call list (newest first) and keep the flagged ones.
 */
async function fetchHandedOverCalls(): Promise<CallData[]> {
  const found: CallData[] = [];
  for (let page = 0; page < MAX_CALL_PAGES; page++) {
    const { calls, total } = await fetchCalls(undefined, CALL_PAGE, page * CALL_PAGE);
    found.push(...calls.filter((c) => c.transferred_to_human || c.is_escalated));
    if (calls.length < CALL_PAGE || (page + 1) * CALL_PAGE >= total) break;
  }
  return found;
}

export function EscalationTracker() {
  const { refreshCounter, selectCall, setSelectedTab } = useDashboardStore();
  const [rows, setRows] = useState<HandoverRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [filterStatus, setFilterStatus] = useState<string>('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadData();
  }, [refreshCounter]);

  async function loadData() {
    setLoading(true);
    try {
      const [escData, handedOver] = await Promise.all([
        fetchEscalations(undefined, 200),
        fetchHandedOverCalls().catch(() => [] as CallData[]),
      ]);
      const hasRow = new Set(escData.escalations.map((e) => e.call_id));
      const fromTable: HandoverRow[] = escData.escalations.map((e) => ({
        key: `esc-${e.id}`,
        call_id: e.call_id,
        status: e.status || 'requested',
        reason: e.reason,
        caller_number: e.caller_number ?? null,
        transferred_to: e.transferred_to,
        agent: null,
        requested_at: e.requested_at,
        completed_at: e.completed_at,
      }));
      const fromCalls: HandoverRow[] = handedOver
        .filter((c) => !hasRow.has(c.id))
        .map((c) => ({
          key: `call-${c.id}`,
          call_id: c.id,
          status: 'transferred',
          reason: c.summary || (c.intent && c.intent.toLowerCase() !== 'unknown' ? c.intent : null) || 'Call handed over to a person',
          caller_number: c.caller_number || null,
          transferred_to: null,
          agent: c.current_agent || null,
          requested_at: c.ended_at || c.started_at,
          completed_at: null,
        }));
      setRows([...fromTable, ...fromCalls].sort((a, b) => sortKey(b.requested_at) - sortKey(a.requested_at)));
      setLoaded(true);
    } catch (err) {
      console.error('Failed to load escalations:', err);
    } finally {
      setLoading(false);
    }
  }

  const shown = useMemo(() => (filterStatus ? rows.filter((r) => r.status === filterStatus) : rows), [rows, filterStatus]);

  // Worked out from the list itself so the cards always agree with it. A call flagged as handed over is counted as a
  // completed handoff, but the flag is set when the agent dispatches the transfer and the call record cannot say
  // whether a person picked up, so for those rows the success rate is an upper bound, not a measurement.
  const metrics = useMemo(() => {
    const done = rows.filter((r) => r.status === 'completed' || r.status === 'transferred').length;
    return {
      total: rows.length,
      completed: done,
      failed: rows.filter((r) => r.status === 'failed').length,
      successRate: rows.length ? Math.round((done / rows.length) * 1000) / 10 : 0,
    };
  }, [rows]);

  const statusIcon = (status: string) => {
    switch (status) {
      case 'completed':
        return <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />;
      case 'failed':
        return <XCircle className="w-3.5 h-3.5 text-red-500" />;
      case 'started':
        return <ArrowUpRight className="w-3.5 h-3.5 text-blue-500" />;
      case 'transferred':
        return <ArrowUpRight className="w-3.5 h-3.5 text-violet-500" />;
      default:
        return <Clock className="w-3.5 h-3.5 text-amber-500" />;
    }
  };

  const statusBadge = (status: string) => {
    const styles: Record<string, string> = {
      requested: 'bg-amber-50 text-amber-600 border-amber-200',
      started: 'bg-blue-50 text-blue-600 border-blue-200',
      completed: 'bg-emerald-50 text-emerald-600 border-emerald-200',
      failed: 'bg-red-50 text-red-600 border-red-200',
      transferred: 'bg-violet-50 text-violet-600 border-violet-200',
    };
    return (
      <span
        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border ${
          styles[status] || styles['requested']
        }`}
      >
        {statusIcon(status)}
        {statusLabel(status)}
      </span>
    );
  };

  const filterOptions = ['', 'transferred', 'requested', 'started', 'completed', 'failed'];

  return (
    <div className="flex-1 flex flex-col bg-[#fbfbfa] dark:bg-[#08090e] p-6 overflow-hidden transition-colors duration-300">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white">Escalation Tracker</h2>
          <p className="text-xs text-slate-400 dark:text-stone-500 mt-1">Human handoff & escalation events</p>
        </div>
      </div>

      {/* Metrics Cards */}
      {loaded && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
          <MiniCard label="Total" value={metrics.total} color="slate" />
          <MiniCard label="Completed" value={metrics.completed} color="emerald" />
          <MiniCard label="Failed" value={metrics.failed} color="red" />
          <MiniCard label="Success Rate" value={`${metrics.successRate}%`} color="blue" />
        </div>
      )}

      {/* Filter */}
      <div className="flex flex-wrap gap-2 mb-4">
        {filterOptions.map((opt) => (
          <button
            key={opt || 'all'}
            onClick={() => setFilterStatus(opt)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              filterStatus === opt
                ? 'bg-violet-100 dark:bg-violet-950/60 text-violet-700 dark:text-violet-300 border border-violet-300 dark:border-violet-800'
                : 'bg-white dark:bg-stone-900 text-slate-500 dark:text-stone-400 border border-slate-200 dark:border-white/10 hover:bg-slate-50 dark:hover:bg-stone-800 hover:text-slate-700 dark:hover:text-white'
            }`}
          >
            {opt ? statusLabel(opt) : 'All'}
          </button>
        ))}
      </div>

      {/* Escalation List */}
      <div className="flex-1 overflow-auto space-y-2">
        {loading && rows.length === 0 ? (
          <div className="text-center text-slate-400 text-sm py-12">Loading...</div>
        ) : shown.length === 0 ? (
          <div className="text-center py-12">
            <AlertTriangle className="w-8 h-8 mx-auto mb-2 text-slate-300" />
            <p className="text-slate-400 text-sm">No escalations found</p>
          </div>
        ) : (
          shown.map((esc) => (
            <div
              key={esc.key}
              onClick={() => {
                selectCall(esc.call_id);
                setSelectedTab('live');
              }}
              className="p-4 rounded-2xl bg-white dark:bg-[#0d0f17] border border-stone-200/90 dark:border-white/10 hover:border-stone-400 dark:hover:border-white/30 hover:shadow-xs transition-all cursor-pointer"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    {statusBadge(esc.status)}
                    <span className="text-xs text-stone-400 font-mono">
                      Call #{esc.call_id}
                    </span>
                  </div>
                  <p className="text-xs font-semibold text-stone-800 dark:text-white mt-1 break-words line-clamp-3" title={esc.reason || undefined}>
                    {esc.reason || 'Supervisor assistance requested'}
                  </p>
                  {esc.caller_number && (
                    <p className="text-xs text-stone-500 font-mono mt-1">
                      Caller: {esc.caller_number}
                    </p>
                  )}
                  {esc.agent && (
                    <p className="text-xs text-stone-500 mt-1">
                      Handled by: {esc.agent}
                    </p>
                  )}
                  {esc.transferred_to && (
                    <p className="text-xs text-stone-600 dark:text-stone-300 mt-1 font-medium">
                      Transferred to: {esc.transferred_to}
                    </p>
                  )}
                </div>
                <div className="text-right text-xs text-stone-400 flex-shrink-0">
                  <p>{formatTime(esc.requested_at)}</p>
                  {esc.completed_at && (
                    <p className="text-emerald-700 font-medium mt-0.5">
                      Resolved {formatTime(esc.completed_at)}
                    </p>
                  )}
                  <span className="text-[11px] text-stone-700 font-bold mt-2 inline-block hover:underline">
                    Inspect Call →
                  </span>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function MiniCard({
  label,
  value,
  color,
}: {
  label: string;
  value: number | string;
  color: 'slate' | 'emerald' | 'red' | 'blue';
}) {
  const colorMap = {
    slate: 'border-slate-200',
    emerald: 'border-emerald-200',
    red: 'border-red-200',
    blue: 'border-blue-200',
  };
  const textMap = {
    slate: 'text-slate-800 dark:text-white',
    emerald: 'text-emerald-600',
    red: 'text-red-600',
    blue: 'text-blue-600',
  };

  return (
    <div className={`p-3 rounded-xl bg-white dark:bg-[#0d0f17] border border-stone-200 dark:border-white/10 shadow-sm ${colorMap[color]}`}>
      <p className="text-xs text-slate-400 mb-1">{label}</p>
      <p className={`text-xl font-bold ${textMap[color]}`}>{value}</p>
    </div>
  );
}
