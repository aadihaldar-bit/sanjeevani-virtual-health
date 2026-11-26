'use client';

import React, { useMemo, useState } from 'react';
import { ArrowRight, PhoneOutgoing, RefreshCw, XCircle } from 'lucide-react';
import { api } from '@/lib/adminApi';
import { useDashboardStore } from '@/lib/store';
import { useWorkspace } from '@/lib/useMe';
import { formatWhen, relativeWhen } from '@/lib/format';
import { PatientLabel, useCompanyZone, useListing } from '@/lib/usePatientCare';
import { Badge, Button, Card, EmptyState, Page, Skeleton } from '@/components/ui/kit';

interface Followup {
  id: number;
  patient_id: number;
  patient: PatientLabel | null;
  kind: string;
  status: string;
  due_at: string | null;
  next_attempt_at: string | null;
  attempts: number;
  max_attempts: number;
  last_call_id: number | null;
  outcome: Record<string, unknown>;
}

const KIND: Record<string, string> = { explain: 'Explain medicines', feedback: 'Feedback' };
const STATUS: Record<string, { label: string; tone: 'green' | 'amber' | 'red' | 'grey' | 'blue' }> = {
  pending: { label: 'Waiting', tone: 'blue' },
  calling: { label: 'Calling now', tone: 'amber' },
  done: { label: 'Done', tone: 'green' },
  no_answer: { label: 'Will retry', tone: 'amber' },
  needs_human: { label: 'Needs a person', tone: 'red' },
  cancelled: { label: 'Cancelled', tone: 'grey' },
  patient_declined: { label: 'Declined', tone: 'grey' },
};
const ORDER = ['pending', 'calling', 'no_answer', 'needs_human', 'done', 'cancelled'];

function reasonFor(t: Followup): string {
  const o = t.outcome || {};
  if (o.cancelled_reason === 'patient_do_not_call') return 'Asked not to be called';
  if (o.cancelled_reason === 'prescription_cancelled') return 'Prescription cancelled';
  if (o.cancelled_reason === 'console') return 'Cancelled here';
  if (o.cancelled_reason === 'no_live_agent') return 'No live agent for this company';
  if (o.callback_requested) return `Asked for a call back: ${String(o.callback_requested)}`;
  if (t.status === 'needs_human') return `No success after ${t.attempts} tries`;
  return '';
}

/** The explain and feedback calls the scheduler makes: what is waiting, what is in progress, what happened. */
export function FollowupsPanel() {
  const { me } = useWorkspace();
  const zone = useCompanyZone();
  const { selectCall, setSelectedTab } = useDashboardStore();
  const openCall = (id: number) => { selectCall(id); setSelectedTab('live'); };
  const { rows, failed, reload } = useListing<Followup>('/api/sanjeevani/followups', 'followups');
  const [kind, setKind] = useState('all');
  const [status, setStatus] = useState('all');
  const [busy, setBusy] = useState<number | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  const isAdmin = !!me && (me.is_super_admin || me.role === 'tenant_admin');
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    (rows || []).forEach((t) => { c[t.status] = (c[t.status] || 0) + 1; });
    return c;
  }, [rows]);
  const shown = (rows || []).filter((t) => (kind === 'all' || t.kind === kind) && (status === 'all' || t.status === status));

  const act = async (t: Followup, what: 'run' | 'cancel') => {
    if (what === 'cancel' && !window.confirm('Cancel this call? The patient will not be phoned for it.')) return;
    setBusy(t.id);
    setNotice(null);
    try {
      await api(`/api/sanjeevani/followups/${t.id}/${what}`, { method: 'POST' });
      setNotice({ ok: true, text: what === 'run' ? 'The call is being placed.' : 'Cancelled.' });
      reload();
    } catch (e) {
      setNotice({ ok: false, text: e instanceof Error ? e.message : 'That did not work. Try again.' });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Page title="Follow-up calls" subtitle="The calls that go out to patients after a prescription: one to explain the medicines, and one near the end of the course to ask how they are doing."
      actions={<Button onClick={reload}><RefreshCw className="w-3.5 h-3.5" /> Refresh</Button>}>
      {notice && (
        <div role="status" className={`px-3 py-2 rounded-lg text-[13px] border ${notice.ok ? 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200' : 'border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200'}`}>{notice.text}</div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {ORDER.filter((s) => counts[s]).map((s) => (
          <button key={s} onClick={() => setStatus(status === s ? 'all' : s)} aria-pressed={status === s}
            className={`inline-flex items-center gap-1.5 h-8 px-3 rounded-full border text-[13px] cursor-pointer ${status === s ? 'border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-200' : 'border-stone-200 dark:border-white/10 text-stone-700 dark:text-stone-300 hover:bg-stone-50 dark:hover:bg-white/5'}`}>
            {STATUS[s]?.label || s} <span className="tabular-nums font-medium">{counts[s]}</span>
          </button>
        ))}
        <select aria-label="Type of call" value={kind} onChange={(e) => setKind(e.target.value)}
          className="ml-auto h-8 rounded-lg border border-stone-300 dark:border-white/15 bg-white dark:bg-white/5 px-2 text-[13px] cursor-pointer">
          <option value="all">All calls</option>
          <option value="explain">Explain medicines</option>
          <option value="feedback">Feedback</option>
        </select>
      </div>

      <Card padded={false}>
        {rows === null ? (
          <div className="p-5 space-y-3"><Skeleton className="h-10" /><Skeleton className="h-10" /><Skeleton className="h-10" /></div>
        ) : failed ? (
          <EmptyState title="Follow-up calls could not be loaded" text="Check your connection and refresh." />
        ) : shown.length === 0 ? (
          <EmptyState icon={<PhoneOutgoing className="w-5 h-5" strokeWidth={1.75} />}
            title={rows.length === 0 ? 'No follow-up calls yet' : 'Nothing matches these filters'}
            text={rows.length === 0 ? 'When a prescription is added, its calls are scheduled here automatically. Add one under Prescriptions.' : 'Clear a filter to see the rest.'} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="text-left text-[12px] text-stone-500 dark:text-stone-400 border-b border-stone-100 dark:border-white/10">
                  <th className="font-medium px-5 py-3">When</th>
                  <th className="font-medium px-3 py-3">Patient</th>
                  <th className="font-medium px-3 py-3">Call</th>
                  <th className="font-medium px-3 py-3">Status</th>
                  <th className="font-medium px-3 py-3">Tries</th>
                  <th className="px-5 py-3"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100 dark:divide-white/5">
                {shown.map((t) => {
                  const s = STATUS[t.status] || { label: t.status, tone: 'grey' as const };
                  const note = reasonFor(t);
                  const when = t.status === 'no_answer' && t.next_attempt_at ? t.next_attempt_at : t.due_at;
                  return (
                    <tr key={t.id} className="align-top">
                      <td className="px-5 py-3 whitespace-nowrap">
                        <span className="block text-stone-900 dark:text-white">{formatWhen(when, zone) || '—'}</span>
                        <span className="block text-[12px] text-stone-500 dark:text-stone-400">{t.status === 'no_answer' ? 'retry ' : ''}{relativeWhen(when)}</span>
                      </td>
                      <td className="px-3 py-3 min-w-[160px]">
                        <span className="block font-medium text-stone-900 dark:text-white">{t.patient?.name || `Patient ${t.patient_id}`}</span>
                        <span className="block text-[12px] text-stone-500 dark:text-stone-400">{[t.patient?.contact && `with ${t.patient.contact}`, t.patient?.phone].filter(Boolean).join(' · ')}</span>
                      </td>
                      <td className="px-3 py-3"><Badge tone={t.kind === 'feedback' ? 'blue' : 'grey'}>{KIND[t.kind] || t.kind}</Badge></td>
                      <td className="px-3 py-3">
                        <Badge tone={s.tone}>{s.label}</Badge>
                        {note && <span className="block mt-1 text-[12px] text-stone-500 dark:text-stone-400 max-w-[220px]">{note}</span>}
                      </td>
                      <td className="px-3 py-3 tabular-nums text-stone-600 dark:text-stone-300">{t.attempts}/{t.max_attempts}</td>
                      <td className="px-5 py-3 text-right whitespace-nowrap">
                        {t.last_call_id && (
                          <button onClick={() => openCall(t.last_call_id!)} className="inline-flex items-center gap-1 mr-3 text-[12px] text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer">
                            Open call <ArrowRight className="w-3 h-3" />
                          </button>
                        )}
                        {isAdmin && (
                          <>
                            <Button className="!h-7 !px-2.5 text-[12px]" disabled={busy === t.id || ['calling', 'cancelled', 'done'].includes(t.status)} onClick={() => act(t, 'run')}>
                              <PhoneOutgoing className="w-3 h-3" /> Call now
                            </Button>
                            <Button variant="danger" className="!h-7 !px-2.5 text-[12px] ml-1.5" disabled={busy === t.id || ['cancelled', 'done'].includes(t.status)} onClick={() => act(t, 'cancel')}>
                              <XCircle className="w-3 h-3" /> Cancel
                            </Button>
                          </>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </Page>
  );
}
