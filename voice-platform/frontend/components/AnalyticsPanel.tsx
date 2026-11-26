'use client';

import React, { useEffect, useState } from 'react';
import { fetchAnalyticsSummary, fetchDailyAnalytics, fetchSecuritySummary } from '@/lib/api';
import type { SecuritySummary } from '@/lib/api';
import { attackLabel } from '@/components/SecurityBadge';
import { useDashboardStore } from '@/lib/store';
import {
  Phone, CheckCircle2, ArrowUpRight, Percent,
  Clock, Activity, ShieldCheck,
} from 'lucide-react';

export function AnalyticsPanel() {
  const { analytics, setAnalytics, dailyData, setDailyData, refreshCounter } =
    useDashboardStore();

  const [security, setSecurity] = useState<SecuritySummary | null>(null);

  useEffect(() => {
    loadAnalytics();
  }, [refreshCounter]);

  async function loadAnalytics() {
    try {
      const [summary, daily] = await Promise.all([
        fetchAnalyticsSummary(30),
        fetchDailyAnalytics(14),
      ]);
      setAnalytics(summary);
      setDailyData(daily.data);
    } catch (err) {
      console.error('Failed to load analytics:', err);
    }
    try {
      setSecurity(await fetchSecuritySummary(30));
    } catch (err) {
      console.error('Failed to load security summary:', err);
    }
  }

  const maxDailyTotal = Math.max(...(dailyData.map((d) => d.total_calls) || [1]), 1);

  return (
    <div className="flex-1 flex flex-col bg-slate-950 p-6 overflow-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-semibold text-slate-100">Analytics</h2>
          <p className="text-xs text-slate-400 mt-1">Last 30 days overview</p>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard
          icon={<Phone className="w-5 h-5" />}
          label="Total Calls"
          value={analytics?.total_calls ?? 0}
          color="blue"
        />
        <StatCard
          icon={<CheckCircle2 className="w-5 h-5" />}
          label="AI Resolved"
          value={analytics?.ai_resolved_calls ?? 0}
          color="emerald"
        />
        <StatCard
          icon={<ArrowUpRight className="w-5 h-5" />}
          label="Escalated"
          value={analytics?.escalated_calls ?? 0}
          color="amber"
        />
        <StatCard
          icon={<Percent className="w-5 h-5" />}
          label="Transfer Rate"
          value={`${analytics?.transfer_rate_percent ?? 0}%`}
          color="rose"
        />
      </div>

      {/* Secondary Stats */}
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4 mb-8">
        <div className="p-4 rounded-xl bg-slate-900/60 border border-slate-800">
          <div className="flex items-center gap-2 mb-1">
            <Activity className="w-4 h-4 text-cyan-400" />
            <span className="text-xs text-slate-400">Active Now</span>
          </div>
          <p className="text-2xl font-bold text-slate-100">{analytics?.active_calls ?? 0}</p>
        </div>
        <div className="p-4 rounded-xl bg-slate-900/60 border border-slate-800">
          <div className="flex items-center gap-2 mb-1">
            <Clock className="w-4 h-4 text-violet-400" />
            <span className="text-xs text-slate-400">Avg Duration</span>
          </div>
          <p className="text-2xl font-bold text-slate-100">
            {analytics?.average_duration_seconds
              ? `${Math.floor(Math.round(analytics.average_duration_seconds) / 60)}:${String(
                  Math.round(analytics.average_duration_seconds) % 60
                ).padStart(2, '0')}`
              : '—'}
          </p>
        </div>
        <div className="p-4 rounded-xl bg-slate-900/60 border border-slate-800">
          <div className="flex items-center gap-2 mb-1">
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
            <span className="text-xs text-slate-400">Completed</span>
          </div>
          <p className="text-2xl font-bold text-slate-100">{analytics?.completed_calls ?? 0}</p>
        </div>
      </div>

      {/* Security Guard */}
      <div className="p-5 rounded-xl bg-slate-900/60 border border-slate-800 mb-8">
        <div className="flex items-center gap-2 mb-4">
          <ShieldCheck className="w-4 h-4 text-emerald-400" />
          <h3 className="text-sm font-semibold text-slate-200">Security Guard (30 days)</h3>
        </div>
        {!security || security.calls_checked === 0 ? (
          <p className="text-sm text-slate-500">No guard data recorded yet.</p>
        ) : (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
              <div>
                <p className="text-2xl font-bold text-slate-100">{security.turns_checked}</p>
                <p className="text-xs text-slate-400">Caller turns checked</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-slate-100">{security.calls_checked}</p>
                <p className="text-xs text-slate-400">Calls checked</p>
              </div>
              <div>
                <p className={`text-2xl font-bold ${security.flagged_calls > 0 ? 'text-amber-400' : 'text-emerald-400'}`}>
                  {security.flagged_calls}
                </p>
                <p className="text-xs text-slate-400">Flagged calls</p>
              </div>
              <div>
                <p className={`text-2xl font-bold ${security.jailbreak_attempts > 0 ? 'text-rose-400' : 'text-emerald-400'}`}>
                  {security.jailbreak_attempts}
                </p>
                <p className="text-xs text-slate-400">Blocked attempts</p>
              </div>
            </div>
            <div className="flex flex-wrap gap-2 text-xs">
              {['0', '1', '2', '3'].map((lvl) => (
                <span key={lvl} className="px-2 py-1 rounded bg-slate-800 text-slate-300">
                  Severity {lvl}: {security.severity_counts[lvl] ?? 0}
                </span>
              ))}
              {Object.entries(security.attack_types).map(([type, n]) => (
                <span key={type} className="px-2 py-1 rounded bg-rose-500/10 text-rose-300 border border-rose-500/20">
                  {attackLabel(type)}: {n}
                </span>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Daily Trend Chart */}
      <div className="p-5 rounded-xl bg-slate-900/60 border border-slate-800">
        <h3 className="text-sm font-semibold text-slate-200 mb-4">Daily Call Volume (14 days)</h3>
        {dailyData.length === 0 ? (
          <div className="h-40 flex items-center justify-center text-slate-500 text-sm">
            No data available
          </div>
        ) : (
          <div className="flex items-end gap-1.5 h-40">
            {dailyData.map((day) => {
              const totalHeight = (day.total_calls / maxDailyTotal) * 100;
              const escalatedHeight = (day.escalated_calls / maxDailyTotal) * 100;
              const aiHeight = totalHeight - escalatedHeight;

              return (
                <div
                  key={day.date}
                  className="flex-1 flex flex-col items-center gap-1 group"
                  title={`${day.date}: ${day.total_calls} calls (${day.escalated_calls} escalated)`}
                >
                  <div className="w-full flex flex-col justify-end" style={{ height: '128px' }}>
                    {day.total_calls > 0 && (
                      <>
                        <div
                          className="w-full rounded-t bg-emerald-500/70 transition-all group-hover:bg-emerald-500"
                          style={{ height: `${Math.max(aiHeight, 2)}%` }}
                        />
                        {day.escalated_calls > 0 && (
                          <div
                            className="w-full bg-amber-500/70 transition-all group-hover:bg-amber-500"
                            style={{ height: `${Math.max(escalatedHeight, 2)}%` }}
                          />
                        )}
                      </>
                    )}
                    {day.total_calls === 0 && (
                      <div className="w-full rounded-t bg-slate-800" style={{ height: '2%' }} />
                    )}
                  </div>
                  <span className="text-[9px] text-slate-500 leading-none">
                    {Number(day.date.slice(8, 10))}
                  </span>
                </div>
              );
            })}
          </div>
        )}
        <div className="flex items-center gap-4 mt-3 text-xs text-slate-400">
          <div className="flex items-center gap-1">
            <div className="w-2.5 h-2.5 rounded bg-emerald-500/70" />
            AI Resolved
          </div>
          <div className="flex items-center gap-1">
            <div className="w-2.5 h-2.5 rounded bg-amber-500/70" />
            Escalated
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Sub-components ────────────────────────────────────────────

function StatCard({
  icon,
  label,
  value,
  color,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | string;
  color: 'blue' | 'emerald' | 'amber' | 'rose';
}) {
  const colorMap = {
    blue: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
    emerald: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
    amber: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
    rose: 'bg-rose-500/10 text-rose-400 border-rose-500/20',
  };

  const iconColorMap = {
    blue: 'text-blue-400',
    emerald: 'text-emerald-400',
    amber: 'text-amber-400',
    rose: 'text-rose-400',
  };

  return (
    <div className={`p-4 rounded-xl border ${colorMap[color]}`}>
      <div className="flex items-center justify-between mb-3">
        <span className={iconColorMap[color]}>{icon}</span>
      </div>
      <p className="text-2xl font-bold text-slate-100">{value}</p>
      <p className="text-xs text-slate-400 mt-1">{label}</p>
    </div>
  );
}
