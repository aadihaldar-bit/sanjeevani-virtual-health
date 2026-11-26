'use client';

import React, { useState } from 'react';
import { ArrowRight, CalendarCheck, RefreshCw } from 'lucide-react';
import { api } from '@/lib/adminApi';
import { useDashboardStore } from '@/lib/store';
import { useWorkspace } from '@/lib/useMe';
import { formatWhen } from '@/lib/format';
import { PatientLabel, useCompanyZone, useListing } from '@/lib/usePatientCare';
import { Badge, Button, Card, EmptyState, Page, Skeleton } from '@/components/ui/kit';

interface Appointment {
  id: number;
  patient: PatientLabel | null;
  patient_name: string | null;
  doctor_name: string | null;
  specialty: string | null;
  reason: string | null;
  status: string;
  booked_slot: string | null;
  external_ref: string | null;
  insurer: string | null;
  fee_amount: number | null;
  copay_amount: number | null;
  payment_status: string | null;
  created_at: string | null;
  completed_at: string | null;
  call_id: number | null;
}

const STATUS: Record<string, { label: string; tone: 'blue' | 'green' | 'grey' | 'amber' }> = {
  booked: { label: 'Booked', tone: 'blue' },
  done: { label: 'Visit done', tone: 'green' },
  cancelled: { label: 'Cancelled', tone: 'grey' },
  requested: { label: 'To confirm', tone: 'amber' },
};

/** Appointments the agent booked for patients who phoned in. Marking one done is what sends the patient a feedback call two days later. */
export function AppointmentsPanel() {
  const { me } = useWorkspace();
  const zone = useCompanyZone();
  const { selectCall, setSelectedTab } = useDashboardStore();
  const openCall = (id: number) => { selectCall(id); setSelectedTab('live'); };
  const list = useListing<Appointment>('/api/sanjeevani/meetings?source=inbound', 'meetings');
  const isAdmin = !!me && (me.is_super_admin || me.role === 'tenant_admin');
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);

  const setStatus = async (a: Appointment, status: string) => {
    setSaving(true); setError(''); setNote('');
    try {
      const res = await api<{ feedback_call_due_at?: string | null }>(`/api/sanjeevani/meetings/${a.id}`, { method: 'PUT', body: { status } });
      if (status === 'done') {
        setNote(res.feedback_call_due_at ? `Marked done. A feedback call to the patient is set for ${formatWhen(res.feedback_call_due_at, zone)}.` : 'Marked done.');
      }
      list.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That could not be saved.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Page title="Appointments" subtitle="Doctor appointments booked on calls from patients. Mark a visit done once it has taken place: the patient then gets a feedback call two days later."
      actions={<Button onClick={list.reload}><RefreshCw className="w-3.5 h-3.5" /> Refresh</Button>}>
      {error && <p role="alert" className="text-[13px] text-red-700 dark:text-red-300">{error}</p>}
      {note && <p role="status" className="text-[13px] text-emerald-700 dark:text-emerald-300">{note}</p>}
      <Card padded={false} title="Booked by the agent">
        {list.rows === null ? (
          <div className="p-5 pt-3 space-y-3"><Skeleton className="h-10" /><Skeleton className="h-10" /></div>
        ) : list.rows.length === 0 ? (
          <EmptyState icon={<CalendarCheck className="w-5 h-5" strokeWidth={1.75} />} title="No appointments yet"
            text="When a patient phones in and books a doctor, the appointment appears here with the fee and insurance." />
        ) : (
          <ul className="mt-3 divide-y divide-stone-100 dark:divide-white/5">
            {list.rows.map((a) => {
              const s = STATUS[a.status] || { label: a.status, tone: 'grey' as const };
              return (
                <li key={a.id} className="flex flex-wrap items-start gap-x-4 gap-y-2 px-5 py-3">
                  <div className="min-w-0 flex-1 basis-64">
                    <p className="text-[13px] font-medium text-stone-900 dark:text-white">
                      {a.patient?.name || a.patient_name || 'Patient'} · {a.doctor_name}{a.specialty ? ` (${a.specialty})` : ''}
                    </p>
                    <p className="text-[13px] text-stone-600 dark:text-stone-300">{a.booked_slot || 'No time'}{a.reason ? ` · ${a.reason}` : ''}</p>
                    <p className="text-[12px] text-stone-500 dark:text-stone-400">
                      {[a.external_ref, a.fee_amount != null ? `Fee ${a.fee_amount}` : '',
                        a.insurer ? `${a.insurer}, patient pays ${a.copay_amount ?? a.fee_amount}` : (a.fee_amount != null ? 'no insurance' : ''),
                        a.payment_status === 'to_pay_at_visit' ? 'pays at the visit' : a.payment_status === 'paid' ? 'paid' : '',
                        formatWhen(a.created_at, zone) && `booked ${formatWhen(a.created_at, zone)}`].filter(Boolean).join(' · ')}
                    </p>
                  </div>
                  <Badge tone={s.tone}>{s.label}</Badge>
                  <div className="flex items-center gap-1.5">
                    {a.call_id && (
                      <button onClick={() => openCall(a.call_id!)} className="inline-flex items-center gap-1 mr-1 text-[12px] text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer">
                        Open call <ArrowRight className="w-3 h-3" />
                      </button>
                    )}
                    {isAdmin && a.status === 'booked' && <Button className="!h-7 !px-2.5 text-[12px]" disabled={saving} onClick={() => setStatus(a, 'done')}>Mark visit done</Button>}
                    {isAdmin && a.status === 'booked' && <Button variant="ghost" className="!h-7 !px-2.5 text-[12px]" disabled={saving} onClick={() => setStatus(a, 'cancelled')}>Cancel</Button>}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </Page>
  );
}
