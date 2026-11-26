'use client';

import React, { useRef, useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight, Download, FileText, RefreshCw, Upload } from 'lucide-react';
import { authFetch } from '@/lib/authFetch';
import { getApiBase } from '@/lib/api';
import { useWorkspace } from '@/lib/useMe';
import { formatDay } from '@/lib/format';
import { PatientLabel, useCompanyZone, useListing } from '@/lib/usePatientCare';
import { Badge, Button, Card, EmptyState, Page, Skeleton } from '@/components/ui/kit';
import { DraftPrescription, PrescriptionReview } from './PrescriptionReview';

interface Item {
  id: number;
  drug_name: string | null;
  strength: string | null;
  form: string | null;
  dose_text: string | null;
  timing: string | null;
  duration_days: number | null;
  end_date: string | null;
  status: string;
  open_question: string | null;
}
interface Prescription {
  id: number;
  external_id: string | null;
  patient: PatientLabel | null;
  doctor_name: string | null;
  facility: string | null;
  status: string;
  issued_at: string | null;
  items: Item[];
}

const STATUS: Record<string, { label: string; tone: 'green' | 'amber' | 'grey' | 'blue' }> = {
  draft: { label: 'Needs review', tone: 'amber' },
  issued: { label: 'Waiting for the first call', tone: 'blue' },
  explained: { label: 'Explained', tone: 'amber' },
  completed: { label: 'Course finished', tone: 'green' },
  cancelled: { label: 'Cancelled', tone: 'grey' },
};

/** The file the page accepts, with one example a person can start from. The start date is today so the end date can be worked out. */
function exampleJson(): string {
  const today = new Date().toISOString().slice(0, 10);
  return JSON.stringify([{
    patient: { name: 'Aadi Haldar' },
    prescription: { external_id: 'RX-1001', doctor_name: 'Dr. Sonia', facility: 'Sanjeevani', course_days: 5, extra_instruction: 'Keep a temperature chart.' },
    items: [
      { drug_name: 'Crocin DS Syrup', form: 'syrup', dose_text: '4 ml', frequency_per_day: 2, timing: 'morning and evening', start_date: today, duration_days: 5,
        plain_summary_en: 'The syrup for the fever and any aches. Give 4 millilitres in the morning and 4 in the evening.', status: 'confirmed' },
      { drug_name: 'Ephodrex Syrup', form: 'syrup', start_date: today, duration_days: 5, status: 'needs_clarification', open_question: 'The note shows 3.5 ml and 4 ml. Which is the dose?' },
    ],
  }], null, 2);
}
const EXAMPLE_CSV = 'patient_external_id,first_name,guardian_name,phone_e164,identity_dob,preferred_language,prescription_external_id,doctor_name,facility,drug_name,strength,form,dose_text,frequency_per_day,timing,start_date,duration_days,plain_summary_en\n'
  + 'PAT-1001,Aarav,Rahul,+971500000000,2023-03-14,en,RX-1001,Dr. Sonia,Sanjeevani,Crocin DS Syrup,,syrup,4 ml,2,morning and evening,2026-10-24,5,The syrup for the fever and any aches.\n';

function download(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}

/** Prescriptions in, calls out: add a file here (or have the hospital system push them) and the calls are scheduled. */
export function PrescriptionsPanel() {
  const { me } = useWorkspace();
  const zone = useCompanyZone();
  const { rows, failed, reload } = useListing<Prescription>('/api/sanjeevani/prescriptions', 'prescriptions');
  const fileRef = useRef<HTMLInputElement>(null);
  const pdfRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [parsingPdf, setParsingPdf] = useState(false);
  const [review, setReview] = useState<DraftPrescription | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const isAdmin = !!me && (me.is_super_admin || me.role === 'tenant_admin');

  const uploadPdf = async (file: File) => {
    setParsingPdf(true);
    setNotice(null);
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await authFetch(`${getApiBase()}/api/sanjeevani/prescriptions/parse-pdf`, { method: 'POST', body: form });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `The PDF could not be read (error ${res.status}).`);
      if (body.review_skipped) {
        // Review turned off: the parse is saved straight away — show it in the list, no dialog.
        setNotice({ ok: true, text: 'PDF read and saved (review is off). Open it below to check how the medicines were parsed.' });
      } else if (body.prescription) {
        setReview(body.prescription as DraftPrescription);
      }
      reload();
    } catch (e) {
      setNotice({ ok: false, text: e instanceof Error ? e.message : 'The PDF could not be read.' });
    } finally {
      setParsingPdf(false);
      if (pdfRef.current) pdfRef.current.value = '';
    }
  };

  const upload = async (file: File) => {
    setUploading(true);
    setNotice(null);
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await authFetch(`${getApiBase()}/api/sanjeevani/prescriptions/upload`, { method: 'POST', body: form });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `The upload failed (error ${res.status}).`);
      const results: { status: string; tasks_created?: number; detail?: string }[] = body.results || [];
      const fresh = results.filter((r) => r.status === 'created').length;
      const missing = results.filter((r) => r.status === 'patient_not_found');
      const calls = results.reduce((n, r) => n + (r.tasks_created || 0), 0);
      const base = `${fresh} new ${fresh === 1 ? 'prescription' : 'prescriptions'}, ${results.length - fresh - missing.length} already on file, ${calls} ${calls === 1 ? 'call' : 'calls'} scheduled.`;
      setNotice(missing.length ? { ok: false, text: `${base} Not saved: ${missing.map((m) => m.detail).join(' ')} Add them under Patients first.` } : { ok: true, text: fresh ? `${base} The first call goes out in about three minutes.` : base });
      reload();
    } catch (e) {
      setNotice({ ok: false, text: e instanceof Error ? e.message : 'The upload failed.' });
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  return (
    <Page title="Prescriptions" subtitle="Each prescription schedules two calls: one to explain the medicines, and one near the end of the course to ask how the patient is doing."
      actions={<Button onClick={reload}><RefreshCw className="w-3.5 h-3.5" /> Refresh</Button>}>
      {notice && (
        <div role="status" className={`px-3 py-2 rounded-lg text-[13px] border ${notice.ok ? 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200' : 'border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200'}`}>{notice.text}</div>
      )}

      {isAdmin && (
        <Card title="Add prescriptions">
          <p className="mt-2 text-[13px] text-stone-500 dark:text-stone-400">
            Upload a CSV or JSON file. Sending the same prescription again does not duplicate it. A medicine with no start date and length gets no feedback call, because its end date cannot be worked out.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <input ref={fileRef} type="file" accept=".csv,.json,text/csv,application/json" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); }} />
            <input ref={pdfRef} type="file" accept=".pdf,application/pdf" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadPdf(f); }} />
            <Button variant="primary" disabled={uploading} onClick={() => fileRef.current?.click()}><Upload className="w-3.5 h-3.5" /> {uploading ? 'Uploading...' : 'Choose a file'}</Button>
            <Button disabled={parsingPdf} onClick={() => pdfRef.current?.click()}><FileText className="w-3.5 h-3.5" /> {parsingPdf ? 'Reading PDF…' : 'Read a PDF prescription'}</Button>
            <Button onClick={() => download('prescriptions-example.json', exampleJson(), 'application/json')}><Download className="w-3.5 h-3.5" /> Example (JSON)</Button>
            <Button onClick={() => download('prescriptions-example.csv', EXAMPLE_CSV, 'text/csv')}><Download className="w-3.5 h-3.5" /> Example (CSV)</Button>
          </div>
          <p className="mt-3 text-[12px] text-stone-500 dark:text-stone-400">
            A digital PDF (not a scan or photo) is read by the assistant into a draft you review before any call goes out — doses it cannot read clearly are held back for a pharmacist. This holds patient information: only upload files you are allowed to share, and use made-up patients for demos.
          </p>
        </Card>
      )}

      <Card padded={false} title="Prescriptions on file">
        {rows === null ? (
          <div className="p-5 pt-3 space-y-3"><Skeleton className="h-10" /><Skeleton className="h-10" /></div>
        ) : failed ? (
          <EmptyState title="Prescriptions could not be loaded" text="Check your connection and refresh." />
        ) : rows.length === 0 ? (
          <EmptyState icon={<FileText className="w-5 h-5" strokeWidth={1.75} />} title="No prescriptions yet"
            text={isAdmin ? 'Upload a file above, or have your hospital system send them to the prescriptions API.' : 'An administrator adds prescriptions here.'} />
        ) : (
          <ul className="mt-3 divide-y divide-stone-100 dark:divide-white/5">
            {rows.map((p) => {
              const s = STATUS[p.status] || { label: p.status, tone: 'grey' as const };
              const isDraft = p.status === 'draft';
              const isOpen = open === p.id;
              const unclear = p.items.filter((i) => i.status === 'needs_clarification').length;
              return (
                <li key={p.id}>
                  <button onClick={() => { if (isDraft && isAdmin) setReview(p as unknown as DraftPrescription); else setOpen(isOpen ? null : p.id); }} aria-expanded={isOpen}
                    className="w-full flex items-center gap-3 px-5 py-3 text-left hover:bg-stone-50 dark:hover:bg-white/5 cursor-pointer">
                    {isOpen ? <ChevronDown className="w-4 h-4 text-stone-400 flex-shrink-0" /> : <ChevronRight className="w-4 h-4 text-stone-400 flex-shrink-0" />}
                    <span className="min-w-0 flex-1">
                      <span className="block text-[13px] font-medium text-stone-900 dark:text-white truncate">
                        {p.patient?.name || 'Patient'}{p.patient?.contact ? ` · with ${p.patient.contact}` : ''}
                      </span>
                      <span className="block text-[12px] text-stone-500 dark:text-stone-400 truncate">
                        {[p.doctor_name, p.external_id, formatDay(p.issued_at, zone)].filter(Boolean).join(' · ')}
                      </span>
                    </span>
                    {unclear > 0 && <Badge tone="amber"><AlertTriangle className="w-3 h-3" /> {unclear} to confirm</Badge>}
                    <span className="text-[12px] text-stone-500 dark:text-stone-400 whitespace-nowrap">{p.items.length} {p.items.length === 1 ? 'medicine' : 'medicines'}</span>
                    <Badge tone={s.tone}>{s.label}</Badge>
                    {isDraft && isAdmin && <span className="text-[12px] font-medium text-amber-700 dark:text-amber-300 whitespace-nowrap">Review →</span>}
                  </button>
                  {isOpen && (
                    <div className="px-5 pb-4 pl-12">
                      <table className="w-full text-[13px]">
                        <thead><tr className="text-left text-[12px] text-stone-500 dark:text-stone-400"><th className="font-medium py-1.5 pr-3">Medicine</th><th className="font-medium py-1.5 pr-3">Dose</th><th className="font-medium py-1.5 pr-3">When</th><th className="font-medium py-1.5 pr-3">Ends</th></tr></thead>
                        <tbody className="divide-y divide-stone-100 dark:divide-white/5">
                          {p.items.map((i) => (
                            <tr key={i.id} className="align-top">
                              <td className="py-2 pr-3 text-stone-900 dark:text-white">{[i.drug_name, i.strength].filter(Boolean).join(' ') || 'Medicine'}</td>
                              <td className="py-2 pr-3">
                                {i.status === 'needs_clarification'
                                  ? <span className="text-amber-700 dark:text-amber-300">To confirm{i.open_question ? `: ${i.open_question}` : ''}</span>
                                  : (i.dose_text || '—')}
                              </td>
                              <td className="py-2 pr-3 text-stone-600 dark:text-stone-300">{i.timing || '—'}</td>
                              <td className="py-2 pr-3 text-stone-600 dark:text-stone-300">{i.end_date ? formatDay(i.end_date, zone) : (i.duration_days ? `${i.duration_days} days` : 'Not known')}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      {review && (
        <PrescriptionReview draft={review} onClose={() => setReview(null)}
          onConfirmed={() => {
            setReview(null);
            setNotice({ ok: true, text: 'Prescription confirmed. The first call goes out in about three minutes.' });
            reload();
          }} />
      )}
    </Page>
  );
}
