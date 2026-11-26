'use client';

import React, { useEffect, useState } from 'react';
import { api } from '@/lib/adminApi';
import type { TabId } from '@/lib/store';
import { Skeleton } from '@/components/ui/kit';

interface Summary {
  prescriptions: number;
  explain_calls_done: number;
  feedback_calls_made: number;
  feedback_calls_answered: number;
  percent_recovered: number | null;
  average_satisfaction: number | null;
  meetings_requested: number;
  meetings_booked: number;
  unreachable_patients: number;
  side_effects_by_medicine: { medicine: string; mentions: number }[];
  tasks_by_status: Record<string, number>;
}

/** The programme at a glance, for a company that follows patients up after a prescription. */
export function PatientCareFigures({ onGo }: { onGo: (tab: TabId) => void }) {
  const [s, setS] = useState<Summary | null | undefined>(undefined);
  useEffect(() => {
    let cancelled = false;
    api<Summary>('/api/sanjeevani/reports/summary').then((r) => { if (!cancelled) setS(r); }).catch(() => { if (!cancelled) setS(null); });
    return () => { cancelled = true; };
  }, []);
  if (s === null) return null;

  const waiting = (s?.tasks_by_status.pending || 0) + (s?.tasks_by_status.no_answer || 0);
  const tiles: { label: string; value: string; hint: string; go?: TabId }[] = s ? [
    { label: 'Prescriptions followed', value: String(s.prescriptions), hint: `${s.explain_calls_done} explained by phone`, go: 'sanjeevani-prescriptions' },
    { label: 'Feedback calls answered', value: `${s.feedback_calls_answered} of ${s.feedback_calls_made}`, hint: `${waiting} more waiting to be placed`, go: 'sanjeevani-followups' },
    { label: 'Recovered', value: s.percent_recovered === null ? '—' : `${Math.round(s.percent_recovered)}%`, hint: 'of patients who answered', go: 'sanjeevani-feedback' },
    { label: 'Satisfaction', value: s.average_satisfaction === null ? '—' : `${s.average_satisfaction.toFixed(1)} / 5`, hint: 'average rating', go: 'sanjeevani-feedback' },
    { label: 'Doctor meetings', value: String(s.meetings_requested), hint: `${s.meetings_booked} booked`, go: 'sanjeevani-feedback' },
    { label: 'Could not be reached', value: String(s.unreachable_patients), hint: 'patients still without an answer', go: 'sanjeevani-followups' },
  ] : [];

  return (
    <section aria-label="Patient care">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 mb-3">
        <h3 className="text-[13px] font-semibold text-stone-900 dark:text-white">Patient care</h3>
        <span className="text-[12px] text-stone-500 dark:text-stone-400">All follow-up calls so far</span>
      </div>
      <div className="grid gap-3 grid-cols-2 lg:grid-cols-[repeat(auto-fit,minmax(170px,1fr))]">
        {s === undefined
          ? Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-[92px] rounded-xl" />)
          : tiles.map((t) => (
            <button key={t.label} onClick={() => t.go && onGo(t.go)}
              className="rounded-xl border border-stone-200 dark:border-white/10 bg-white dark:bg-[#0f121a] p-4 text-left min-w-0 cursor-pointer hover:border-stone-300 dark:hover:border-white/20 transition-colors">
              <span className="block text-[13px] text-stone-500 dark:text-stone-400">{t.label}</span>
              <span className="block mt-2 text-[22px] leading-7 font-medium tabular-nums text-stone-900 dark:text-white">{t.value}</span>
              <span className="block mt-1 text-[12px] text-stone-500 dark:text-stone-400">{t.hint}</span>
            </button>
          ))}
      </div>
      {s && s.side_effects_by_medicine.length > 0 && (
        <p className="mt-2 text-[12px] text-stone-500 dark:text-stone-400">
          Side effects mentioned: {s.side_effects_by_medicine.slice(0, 4).map((x) => `${x.medicine} (${x.mentions})`).join(', ')}.
        </p>
      )}
    </section>
  );
}
