'use client';

import React, { useState } from 'react';
import { ArrowRight, CalendarCheck, HeartPulse, RefreshCw } from 'lucide-react';
import { api } from '@/lib/adminApi';
import { useDashboardStore } from '@/lib/store';
import { useWorkspace } from '@/lib/useMe';
import { formatWhen } from '@/lib/format';
import { PatientLabel, useCompanyZone, useListing } from '@/lib/usePatientCare';
import { Badge, Button, Card, EmptyState, Page, Skeleton } from '@/components/ui/kit';
import { Dialog, FormField, inputClass } from '@/components/ui/Dialog';

interface Feedback {
  id: number;
  medicine: string | null;
  doctor_name: string | null;
  patient: PatientLabel | null;
  taken: string | null;
  helped: string | null;
  satisfaction: number | null;
  side_effects_text: string | null;
  comments?: string | null;
  about_visit?: boolean;
  wants_doctor: boolean;
  refill_requested: boolean;
  created_at: string | null;
  call_id: number | null;
}
interface Meeting {
  id: number;
  patient: PatientLabel | null;
  doctor_name: string | null;
  reason: string | null;
  preferred_times: string[] | null;
  status: string;
  booked_slot: string | null;
  created_at: string | null;
  call_id: number | null;
}

const MEETING: Record<string, { label: string; tone: 'blue' | 'green' | 'grey' | 'amber' }> = {
  requested: { label: 'To confirm', tone: 'amber' },
  booked: { label: 'Booked', tone: 'blue' },
  done: { label: 'Done', tone: 'green' },
  cancelled: { label: 'Cancelled', tone: 'grey' },
};
const HELPED: Record<string, { label: string; tone: 'green' | 'amber' | 'red' }> = {
  yes: { label: 'Helped', tone: 'green' }, partly: { label: 'Helped a little', tone: 'amber' }, no: { label: 'Did not help', tone: 'red' },
};

/** What patients said on the feedback calls, and the doctor meetings it led to. */
export function FeedbackPanel() {
  const { me } = useWorkspace();
  const zone = useCompanyZone();
  const { selectCall, setSelectedTab } = useDashboardStore();
  const openCall = (id: number) => { selectCall(id); setSelectedTab('live'); };
  const meetings = useListing<Meeting>('/api/sanjeevani/meetings', 'meetings');
  const feedback = useListing<Feedback>('/api/sanjeevani/feedback', 'feedback');
  const isAdmin = !!me && (me.is_super_admin || me.role === 'tenant_admin');
  const [booking, setBooking] = useState<Meeting | null>(null);
  const [slot, setSlot] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  const setStatus = async (m: Meeting, status: string, booked_slot?: string) => {
    setSaving(true);
    setError('');
    try {
      await api(`/api/sanjeevani/meetings/${m.id}`, { method: 'PUT', body: { status, ...(booked_slot !== undefined ? { booked_slot } : {}) } });
      setBooking(null);
      meetings.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That could not be saved.');
    } finally {
      setSaving(false);
    }
  };

  const open = (meetings.rows || []).filter((m) => m.status === 'requested').length;
  const reload = () => { meetings.reload(); feedback.reload(); };

  return (
    <Page title="Patient feedback" subtitle="How patients got on with their medicines, and the doctor meetings that followed from it."
      actions={<Button onClick={reload}><RefreshCw className="w-3.5 h-3.5" /> Refresh</Button>}>
      <Card padded={false} title={`Doctor meetings${open ? ` · ${open} to confirm` : ''}`}>
        {meetings.rows === null ? (
          <div className="p-5 pt-3 space-y-3"><Skeleton className="h-10" /><Skeleton className="h-10" /></div>
        ) : meetings.rows.length === 0 ? (
          <EmptyState icon={<CalendarCheck className="w-5 h-5" strokeWidth={1.75} />} title="No meetings requested"
            text="When a patient is not happy with how a medicine worked, the agent asks for a meeting with their doctor and it appears here." />
        ) : (
          <ul className="mt-3 divide-y divide-stone-100 dark:divide-white/5">
            {meetings.rows.map((m) => {
              const s = MEETING[m.status] || { label: m.status, tone: 'grey' as const };
              return (
                <li key={m.id} className="flex flex-wrap items-start gap-x-4 gap-y-2 px-5 py-3">
                  <div className="min-w-0 flex-1 basis-64">
                    <p className="text-[13px] font-medium text-stone-900 dark:text-white">
                      {m.patient?.name || 'Patient'}{m.doctor_name ? ` · ${m.doctor_name}` : ''}
                    </p>
                    <p className="text-[13px] text-stone-600 dark:text-stone-300">{m.reason || 'No reason given'}</p>
                    <p className="text-[12px] text-stone-500 dark:text-stone-400">
                      {[m.preferred_times?.length ? `Prefers ${m.preferred_times.join(' or ')}` : '', m.booked_slot ? `Booked for ${m.booked_slot}` : '', formatWhen(m.created_at, zone) && `asked ${formatWhen(m.created_at, zone)}`].filter(Boolean).join(' · ')}
                    </p>
                  </div>
                  <Badge tone={s.tone}>{s.label}</Badge>
                  <div className="flex items-center gap-1.5">
                    {m.call_id && (
                      <button onClick={() => openCall(m.call_id!)} className="inline-flex items-center gap-1 mr-1 text-[12px] text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer">
                        Open call <ArrowRight className="w-3 h-3" />
                      </button>
                    )}
                    {isAdmin && m.status === 'requested' && <Button className="!h-7 !px-2.5 text-[12px]" onClick={() => { setError(''); setSlot(''); setBooking(m); }}>Confirm a time</Button>}
                    {isAdmin && m.status === 'booked' && <Button className="!h-7 !px-2.5 text-[12px]" disabled={saving} onClick={() => setStatus(m, 'done')}>Mark done</Button>}
                    {isAdmin && ['requested', 'booked'].includes(m.status) && <Button variant="ghost" className="!h-7 !px-2.5 text-[12px]" disabled={saving} onClick={() => setStatus(m, 'cancelled')}>Cancel</Button>}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Card padded={false} title="What patients said">
        {feedback.rows === null ? (
          <div className="p-5 pt-3 space-y-3"><Skeleton className="h-10" /><Skeleton className="h-10" /></div>
        ) : feedback.rows.length === 0 ? (
          <EmptyState icon={<HeartPulse className="w-5 h-5" strokeWidth={1.75} />} title="No feedback yet"
            text="After a course ends the agent calls to ask how the patient is. Their answers appear here." />
        ) : (
          <ul className="mt-3 divide-y divide-stone-100 dark:divide-white/5">
            {feedback.rows.map((f) => {
              const h = f.helped ? HELPED[f.helped] : null;
              return (
                <li key={f.id} className="flex flex-wrap items-start gap-x-4 gap-y-2 px-5 py-3">
                  <div className="min-w-0 flex-1 basis-64">
                    <p className="text-[13px] font-medium text-stone-900 dark:text-white">{f.patient?.name || 'Patient'}{f.medicine ? ` · ${f.medicine}` : f.about_visit ? ' · after a doctor visit' : ''}</p>
                    <p className="text-[13px] text-stone-600 dark:text-stone-300">
                      {f.side_effects_text || f.comments ? `"${f.side_effects_text || f.comments}"` : f.taken === 'no' || f.taken === 'not_taken' ? 'Did not take it' : 'No side effects mentioned'}
                    </p>
                    <p className="text-[12px] text-stone-500 dark:text-stone-400">{[f.doctor_name, formatWhen(f.created_at, zone)].filter(Boolean).join(' · ')}</p>
                  </div>
                  {h && <Badge tone={h.tone}>{h.label}</Badge>}
                  {f.satisfaction !== null && <span className="text-[13px] tabular-nums text-stone-700 dark:text-stone-200 whitespace-nowrap">{f.satisfaction}/5</span>}
                  {f.wants_doctor && <Badge tone="amber">Wants a doctor</Badge>}
                  {f.refill_requested && <Badge tone="blue">Needs a refill</Badge>}
                  {f.call_id && (
                    <button onClick={() => openCall(f.call_id!)} className="inline-flex items-center gap-1 text-[12px] text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer">
                      Open call <ArrowRight className="w-3 h-3" />
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Dialog open={!!booking} onClose={() => !saving && setBooking(null)} title="Confirm the meeting"
        description={booking ? `${booking.patient?.name || 'The patient'} will see ${booking.doctor_name || 'their doctor'}. Enter the time you agreed with the clinic.` : undefined}
        footer={(
          <>
            <Button onClick={() => setBooking(null)} disabled={saving}>Cancel</Button>
            <Button variant="primary" disabled={!slot.trim() || saving} onClick={() => booking && setStatus(booking, 'booked', slot.trim())}>Save</Button>
          </>
        )}>
        <div className="pb-3 space-y-3">
          <FormField label="Date and time" hint={booking?.preferred_times?.length ? `The patient asked for: ${booking.preferred_times.join(' or ')}` : undefined}>
            <input className={inputClass} value={slot} onChange={(e) => setSlot(e.target.value)} placeholder="Wednesday 11:00 AM, by phone" maxLength={120} />
          </FormField>
          {error && <p role="alert" className="text-[13px] text-rose-600 dark:text-rose-400">{error}</p>}
        </div>
      </Dialog>
    </Page>
  );
}
