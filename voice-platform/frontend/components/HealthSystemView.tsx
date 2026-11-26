'use client';

import { useEffect, useState } from 'react';
import {
  HeartPulse, Stethoscope, CalendarCheck, User, Phone, Clock,
  RefreshCw, UserPlus, UserCheck, FileText, ShieldCheck, ShieldAlert, ShieldQuestion,
} from 'lucide-react';
import {
  fetchHealthCalls, fetchHealthCallDetail,
  type CallData, type CallDetail, type AppointmentData, type KycSummary,
} from '@/lib/api';
import { useDashboardStore } from '@/lib/store';

function KycBadge({ kyc }: { kyc?: KycSummary | null }) {
  if (!kyc) return null;
  if (kyc.status === 'verified') {
    return (
      <span title={kyc.demo ? 'Verified with a DEMO credential (not UIDAI)' : kyc.trust === 'document' ? 'Document read + face match. QR is unsigned, so authenticity is NOT proven: check the original ID at the visit' : 'Verified via UIDAI-signed Aadhaar QR + face match'}
        className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full ${kyc.demo || kyc.trust === 'document' ? 'bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-400' : 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400'}`}>
        <ShieldCheck className="w-3 h-3" /> {kyc.demo ? 'KYC (demo)' : kyc.trust === 'document' ? 'ID read (unsigned)' : 'KYC verified'}
      </span>
    );
  }
  if (kyc.status === 'failed' || kyc.status === 'expired') {
    return <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full bg-rose-50 text-rose-600 dark:bg-rose-500/10 dark:text-rose-400"><ShieldAlert className="w-3 h-3" /> KYC {kyc.status}</span>;
  }
  return <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full bg-stone-100 text-stone-500 dark:bg-white/10 dark:text-stone-400"><ShieldQuestion className="w-3 h-3" /> KYC pending</span>;
}

function apptOf(call: { latest_appointment?: AppointmentData | null; appointments?: AppointmentData[] }): AppointmentData | null {
  return call.latest_appointment || (call.appointments && call.appointments[0]) || null;
}

/** The agent books "the assigned doctor" when it has not matched a doctor yet: that is a placeholder, not a name. */
function doctorOf(a: AppointmentData | null): string | null {
  const name = (a?.doctor_name || '').trim();
  return !name || /^the assigned doctor$/i.test(name) ? null : name;
}

const NO_DOCTOR = 'Not assigned yet';

// Timestamps are stored in UTC without a zone marker; the console shows India time.
function fmt(ts: string | null): string {
  if (!ts) return '—';
  const d = new Date(/(Z|[+-]\d{2}:?\d{2})$/.test(ts) ? ts : ts + 'Z');
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export default function HealthSystemView() {
  const refreshCounter = useDashboardStore((s) => (s as any).refreshCounter);
  const [calls, setCalls] = useState<CallData[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<CallDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  async function load() {
    setLoading(true);
    try { const res = await fetchHealthCalls(100, 0); setCalls(res.calls || []); }
    catch (e) { console.error('[Health] list error', e); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [refreshCounter]);

  useEffect(() => {
    if (selectedId == null) { setDetail(null); return; }
    let active = true;
    setDetailLoading(true);
    fetchHealthCallDetail(selectedId)
      .then((d) => { if (active) setDetail(d); })
      .catch((e) => console.error('[Health] detail error', e))
      .finally(() => { if (active) setDetailLoading(false); });
    return () => { active = false; };
  }, [selectedId]);

  const cardCls = 'bg-white dark:bg-[#0d0f17] border border-stone-200/90 dark:border-white/10 rounded-xl shadow-xs';

  return (
    <div className="flex-1 flex flex-col bg-[#fbfbfa] dark:bg-[#08090e] p-6 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-rose-50 dark:bg-rose-500/10 flex items-center justify-center">
            <HeartPulse className="w-5 h-5 text-rose-500 stroke-[1.5]" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-stone-800 dark:text-stone-100">Appointments</h1>
            <p className="text-xs text-stone-500 dark:text-stone-400">Patients booked by your clinic receptionist agent, with their identity check</p>
          </div>
        </div>
        <button onClick={load} className="flex items-center gap-1.5 text-xs text-stone-600 dark:text-stone-300 border border-stone-200 dark:border-white/10 rounded-lg px-3 py-1.5 hover:bg-stone-50 dark:hover:bg-white/5">
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </div>

      <div className="flex-1 flex gap-5 overflow-hidden">
        {/* List */}
        <div className={`${cardCls} w-[380px] shrink-0 flex flex-col overflow-hidden`}>
          <div className="px-4 py-3 border-b border-stone-200/80 dark:border-white/10 text-xs font-medium text-stone-500 dark:text-stone-400">
            {calls.length} appointment call{calls.length === 1 ? '' : 's'}
          </div>
          <div className="flex-1 overflow-y-auto">
            {loading && <div className="p-6 text-sm text-stone-400">Loading…</div>}
            {!loading && calls.length === 0 && (
              <div className="p-6 text-sm text-stone-400">No appointment calls yet. Book one via the medical receptionist agent.</div>
            )}
            {calls.map((c) => {
              const a = apptOf(c);
              const active = selectedId === c.id;
              return (
                <button key={c.id} onClick={() => setSelectedId(c.id)}
                  className={`w-full text-left px-4 py-3 border-b border-stone-100 dark:border-white/5 transition-colors ${active ? 'bg-rose-50/70 dark:bg-rose-500/10' : 'hover:bg-stone-50 dark:hover:bg-white/5'}`}>
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-stone-800 dark:text-stone-100 truncate">
                      {a?.patient_name || c.kyc?.verified_name || c.caller_number || 'Unknown patient'}
                    </span>
                    {a?.is_new_patient
                      ? <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400">New</span>
                      : <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-sky-50 text-sky-600 dark:bg-sky-500/10 dark:text-sky-400">Returning</span>}
                  </div>
                  <div className="mt-1 flex items-center gap-1.5 text-xs text-stone-500 dark:text-stone-400">
                    <Stethoscope className="w-3 h-3" /> {doctorOf(a) || NO_DOCTOR}
                  </div>
                  <div className="mt-0.5 flex items-center gap-1.5 text-xs text-stone-500 dark:text-stone-400">
                    <CalendarCheck className="w-3 h-3" /> {a?.appointment_time || '—'}
                  </div>
                  {c.kyc && <div className="mt-1.5"><KycBadge kyc={c.kyc} /></div>}
                </button>
              );
            })}
          </div>
        </div>

        {/* Detail */}
        <div className="flex-1 overflow-y-auto">
          {!selectedId && (
            <div className={`${cardCls} h-full flex items-center justify-center text-sm text-stone-400`}>
              Select an appointment call to see patient details.
            </div>
          )}
          {selectedId && (
            <div className="space-y-4">
              {detailLoading && <div className="text-sm text-stone-400">Loading details…</div>}
              {detail && (() => {
                const a = apptOf(detail);
                return (
                  <>
                    {/* Patient summary */}
                    <div className={`${cardCls} p-5`}>
                      <div className="flex items-center gap-2 mb-4">
                        {a?.is_new_patient ? <UserPlus className="w-4 h-4 text-emerald-500" /> : <UserCheck className="w-4 h-4 text-sky-500" />}
                        <h2 className="text-sm font-semibold text-stone-800 dark:text-stone-100">Patient Details</h2>
                      </div>
                      <div className="grid grid-cols-2 gap-4 text-sm">
                        <Field icon={<User className="w-3.5 h-3.5" />} label="Name" value={a?.patient_name} />
                        <Field icon={<Phone className="w-3.5 h-3.5" />} label="Contact" value={a?.caller_number || detail.caller_number} />
                        <Field icon={<UserCheck className="w-3.5 h-3.5" />} label="Patient type" value={a?.is_new_patient ? 'New patient' : 'Returning patient'} />
                        <Field icon={<Clock className="w-3.5 h-3.5" />} label="Call time" value={fmt(detail.started_at)} />
                      </div>
                      <div className="mt-4">
                        <div className="text-xs text-stone-400 mb-1">Reported problem</div>
                        <div className="text-sm text-stone-700 dark:text-stone-200 break-words">{a?.problem || detail.summary || '—'}</div>
                      </div>
                    </div>

                    {/* Identity verification (KYC) */}
                    {detail.kyc && (
                      <div className={`${cardCls} p-5`}>
                        <div className="flex items-center gap-2 mb-4">
                          <ShieldCheck className="w-4 h-4 text-emerald-500" />
                          <h2 className="text-sm font-semibold text-stone-800 dark:text-stone-100">Identity Verification</h2>
                          <KycBadge kyc={detail.kyc} />
                        </div>
                        {detail.kyc.status === 'verified' ? (
                          <div className="grid grid-cols-2 gap-4 text-sm">
                            <Field icon={<User className="w-3.5 h-3.5" />} label="Name on Aadhaar" value={detail.kyc.verified_name} />
                            <Field icon={<FileText className="w-3.5 h-3.5" />} label="Aadhaar" value={detail.kyc.aadhaar_last4 ? `XXXX XXXX ${detail.kyc.aadhaar_last4}` : null} />
                            <Field icon={<UserCheck className="w-3.5 h-3.5" />} label="Face match score" value={detail.kyc.face_score != null ? detail.kyc.face_score.toFixed(2) : null} />
                            <Field icon={<ShieldCheck className="w-3.5 h-3.5" />} label="Liveness" value={detail.kyc.liveness_passed ? 'Passed' : 'Not passed'} />
                            <Field icon={<Clock className="w-3.5 h-3.5" />} label="Verified at" value={fmt(detail.kyc.verified_at)} />
                            <Field icon={<FileText className="w-3.5 h-3.5" />} label="Source" value={detail.kyc.demo ? 'DEMO credential (not UIDAI)' : detail.kyc.trust === 'document' ? 'Unsigned Aadhaar document + face match (check original ID at visit)' : 'UIDAI-signed Aadhaar Secure QR'} />
                          </div>
                        ) : (
                          <div className="text-sm text-stone-500 dark:text-stone-400">
                            {detail.kyc.status === 'failed' || detail.kyc.status === 'expired'
                              ? `Verification ${detail.kyc.status}${detail.kyc.failure_reason ? ` (${detail.kyc.failure_reason})` : ''}.`
                              : 'Waiting for the patient to complete verification on their phone.'}
                          </div>
                        )}
                        <div className="mt-3 text-[11px] text-stone-400">No Aadhaar number or photographs are stored — only the fields above.</div>
                      </div>
                    )}

                    {/* Appointment */}
                    <div className={`${cardCls} p-5`}>
                      <div className="flex items-center gap-2 mb-4">
                        <CalendarCheck className="w-4 h-4 text-rose-500" />
                        <h2 className="text-sm font-semibold text-stone-800 dark:text-stone-100">Appointment</h2>
                        {a?.status && <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400 capitalize">{a.status}</span>}
                      </div>
                      <div className="grid grid-cols-2 gap-4 text-sm">
                        <Field icon={<FileText className="w-3.5 h-3.5" />} label="Booking ID" value={a?.appointment_id} />
                        <Field icon={<Stethoscope className="w-3.5 h-3.5" />} label="Doctor" value={doctorOf(a) || NO_DOCTOR} />
                        <Field icon={<HeartPulse className="w-3.5 h-3.5" />} label="Specialty" value={a?.doctor_specialty} />
                        <Field icon={<CalendarCheck className="w-3.5 h-3.5" />} label="Date & time" value={a?.appointment_time} />
                        {a?.notes && <Field wide icon={<FileText className="w-3.5 h-3.5" />} label="Notes" value={a.notes} />}
                      </div>
                    </div>

                    {/* Transcript */}
                    <div className={`${cardCls} p-5`}>
                      <h2 className="text-sm font-semibold text-stone-800 dark:text-stone-100 mb-3">Conversation</h2>
                      <div className="space-y-2 max-h-[320px] overflow-y-auto">
                        {(detail.transcript || []).map((m) => (
                          <div key={m.id} className={`text-sm ${m.role === 'agent' ? 'text-stone-700 dark:text-stone-200' : 'text-stone-500 dark:text-stone-400'}`}>
                            <span className="font-medium">{m.role === 'agent' ? 'Agent' : 'Patient'}:</span> {m.message}
                          </div>
                        ))}
                        {(!detail.transcript || detail.transcript.length === 0) && <div className="text-sm text-stone-400">No transcript.</div>}
                      </div>
                    </div>
                  </>
                );
              })()}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Field({ icon, label, value, wide }: { icon: React.ReactNode; label: string; value?: string | null; wide?: boolean }) {
  return (
    <div className={wide ? 'col-span-2' : undefined}>
      <div className="flex items-center gap-1.5 text-xs text-stone-400 mb-0.5">{icon} {label}</div>
      <div className="text-stone-800 dark:text-stone-100 break-words">{value || '—'}</div>
    </div>
  );
}
