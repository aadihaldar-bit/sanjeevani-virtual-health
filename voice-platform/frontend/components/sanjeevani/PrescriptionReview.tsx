'use client';

import React, { useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { authFetch } from '@/lib/authFetch';
import { getApiBase } from '@/lib/api';
import { Dialog, FormField, inputClass } from '@/components/ui/Dialog';
import { Badge, Button } from '@/components/ui/kit';

interface DraftItem {
  id: number;
  drug_name: string | null;
  strength: string | null;
  form: string | null;
  dose_text: string | null;
  frequency_per_day: number | null;
  timing: string | null;
  duration_days: number | null;
  warnings: string | null;
  plain_summary_en: string | null;
  plain_summary_ar: string | null;
  status: string;
  open_question: string | null;
}
export interface DraftPrescription {
  id: number;
  external_id: string | null;
  doctor_name: string | null;
  facility: string | null;
  status: string;
  items: DraftItem[];
}

/** Review a PDF-read prescription before any call goes out. The reviewer checks every dose the
 *  assistant read, fixes what is wrong, confirms the ones that are right, and supplies the phone and
 *  date of birth. Only on "Confirm & schedule" does the backend create the calls. */
export function PrescriptionReview({ draft, onClose, onConfirmed }: {
  draft: DraftPrescription; onClose: () => void; onConfirmed: () => void;
}) {
  const [doctor, setDoctor] = useState(draft.doctor_name || '');
  const [facility, setFacility] = useState(draft.facility || '');
  const [phone, setPhone] = useState('');
  const [dob, setDob] = useState('');
  const [firstName, setFirstName] = useState('');
  const [guardian, setGuardian] = useState('');
  const [items, setItems] = useState<DraftItem[]>(() => draft.items.map((i) => ({ ...i })));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const setItem = (id: number, patch: Partial<DraftItem>) =>
    setItems((xs) => xs.map((i) => (i.id === id ? { ...i, ...patch } : i)));

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      const payload = {
        patient: {
          first_name: firstName.trim() || undefined,
          guardian_name: guardian.trim() || undefined,
          phone_e164: phone.trim() || undefined,
          identity_dob: dob.trim() || undefined,
        },
        doctor_name: doctor,
        facility,
        items: items.map((i) => ({
          id: i.id,
          drug_name: i.drug_name,
          strength: i.strength,
          dose_text: i.dose_text,
          frequency_per_day: i.frequency_per_day,
          timing: i.timing,
          duration_days: i.duration_days,
          warnings: i.warnings,
          plain_summary_en: i.plain_summary_en,
          plain_summary_ar: i.plain_summary_ar,
          status: i.status,
        })),
      };
      const res = await authFetch(`${getApiBase()}/api/sanjeevani/prescriptions/${draft.id}/confirm`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `Could not confirm (error ${res.status}).`);
      onConfirmed();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not confirm the prescription.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open title="Review the prescription before calling" width="max-w-3xl"
      description="The assistant read this from the PDF. Check every medicine and dose against the prescription. A medicine kept as “confirm this dose” is spoken to the patient; anything left as “pharmacist to confirm” is not."
      onClose={onClose}
      footer={<>
        <Button onClick={onClose} disabled={saving}>Cancel</Button>
        <Button variant="primary" onClick={submit} disabled={saving}>{saving ? 'Scheduling…' : 'Confirm & schedule calls'}</Button>
      </>}>
      <div className="space-y-4">
        <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <span>Read automatically from a PDF. Nothing here is called until you confirm it. Never accept a dose you have not checked against the prescription.</span>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <FormField label="Patient first name"><input className={inputClass} value={firstName} onChange={(e) => setFirstName(e.target.value)} placeholder="e.g. Aarav" /></FormField>
          <FormField label="Parent / guardian"><input className={inputClass} value={guardian} onChange={(e) => setGuardian(e.target.value)} placeholder="e.g. Rahul" /></FormField>
          <FormField label="Phone to call" required hint="The number the patient is reached on"><input className={inputClass} value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+9715…" /></FormField>
          <FormField label="Date of birth" required hint="Used for the identity check on the call"><input className={inputClass} value={dob} onChange={(e) => setDob(e.target.value)} placeholder="YYYY-MM-DD" /></FormField>
          <FormField label="Doctor"><input className={inputClass} value={doctor} onChange={(e) => setDoctor(e.target.value)} /></FormField>
          <FormField label="Facility"><input className={inputClass} value={facility} onChange={(e) => setFacility(e.target.value)} /></FormField>
        </div>

        <div className="space-y-3">
          {items.map((i) => {
            const confirmed = i.status === 'confirmed';
            return (
              <div key={i.id} className="rounded-lg border border-stone-200 dark:border-white/10 p-3 space-y-2">
                <div className="flex items-center gap-2">
                  <input className={inputClass + ' font-medium flex-1'} value={i.drug_name || ''} onChange={(e) => setItem(i.id, { drug_name: e.target.value })} placeholder="Medicine name" />
                  <input className={inputClass + ' w-28'} value={i.strength || ''} onChange={(e) => setItem(i.id, { strength: e.target.value })} placeholder="Strength" />
                </div>
                {i.open_question && <p className="text-[12px] text-amber-700 dark:text-amber-300 flex items-center gap-1"><AlertTriangle className="w-3 h-3" /> {i.open_question}</p>}
                <div className="grid grid-cols-3 gap-2">
                  <FormField label="Dose"><input className={inputClass} value={i.dose_text || ''} onChange={(e) => setItem(i.id, { dose_text: e.target.value })} placeholder="e.g. 4 ml" /></FormField>
                  <FormField label="When / how often"><input className={inputClass} value={i.timing || ''} onChange={(e) => setItem(i.id, { timing: e.target.value })} placeholder="morning and evening" /></FormField>
                  <FormField label="For how many days"><input className={inputClass} type="number" value={i.duration_days ?? ''} onChange={(e) => setItem(i.id, { duration_days: e.target.value ? parseInt(e.target.value, 10) : null })} /></FormField>
                </div>
                <FormField label="Plain-language line the agent says (English)" hint="Pharmacist-approved wording; leave empty to say only the dose and timing">
                  <input className={inputClass} value={i.plain_summary_en || ''} onChange={(e) => setItem(i.id, { plain_summary_en: e.target.value })} placeholder="e.g. The syrup for fever and aches." />
                </FormField>
                <FormField label="Plain-language line the agent says (Arabic)" hint="Spoken to Arabic-speaking patients">
                  <input className={inputClass} dir="rtl" value={i.plain_summary_ar || ''} onChange={(e) => setItem(i.id, { plain_summary_ar: e.target.value })} placeholder="الشراب لخفض الحرارة والألم" />
                </FormField>
                <div className="flex items-center gap-2 pt-1">
                  <button type="button" onClick={() => setItem(i.id, { status: confirmed ? 'needs_clarification' : 'confirmed' })}
                    className={`text-[12px] px-2.5 py-1 rounded-md border cursor-pointer ${confirmed ? 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-200' : 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200'}`}>
                    {confirmed ? '✓ Confirm this dose (will be spoken)' : 'Pharmacist to confirm (not spoken)'}
                  </button>
                  {!confirmed && <Badge tone="amber">Held back</Badge>}
                </div>
              </div>
            );
          })}
        </div>

        {error && <p className="text-[13px] text-rose-700 dark:text-rose-300">{error}</p>}
      </div>
    </Dialog>
  );
}
