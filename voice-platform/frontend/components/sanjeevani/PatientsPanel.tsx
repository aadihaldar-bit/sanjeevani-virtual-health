'use client';

import React, { useState } from 'react';
import { Plus, RefreshCw, Users } from 'lucide-react';
import { api } from '@/lib/adminApi';
import { useWorkspace } from '@/lib/useMe';
import { useListing } from '@/lib/usePatientCare';
import { Badge, Button, Card, EmptyState, Page, Skeleton } from '@/components/ui/kit';
import { Dialog, FormField, inputClass } from '@/components/ui/Dialog';

interface Person {
  id: number;
  username: string | null;
  name: string;
  guardian_name: string | null;
  phone: string | null;
  language: string | null;
  do_not_call: boolean;
  has_dob: boolean;
}

const EMPTY = { username: '', first_name: '', last_name: '', phone_e164: '', identity_dob: '', preferred_language: 'en' };

/** The people the agent may call. A prescription names one of them, and the number and date of birth come from here. */
export function PatientsPanel() {
  const { me } = useWorkspace();
  const { rows, failed, reload } = useListing<Person>('/api/sanjeevani/patients', 'patients');
  const isAdmin = !!me && (me.is_super_admin || me.role === 'tenant_admin');
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const set = (k: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      await api('/api/sanjeevani/patients', { method: 'POST', body: { ...form, username: form.username.trim() || null, last_name: form.last_name.trim() || null } });
      setOpen(false);
      setForm(EMPTY);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That could not be saved.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Page title="Patients" subtitle="Who the agent may call. A prescription names a patient here (by name or username), and the number to ring and the date of birth to check come from this list."
      actions={(
        <div className="flex gap-2">
          <Button onClick={reload}><RefreshCw className="w-3.5 h-3.5" /> Refresh</Button>
          {isAdmin && <Button variant="primary" onClick={() => { setError(''); setOpen(true); }}><Plus className="w-4 h-4" /> Add patient</Button>}
        </div>
      )}>
      <Card padded={false}>
        {rows === null ? (
          <div className="p-5 space-y-3"><Skeleton className="h-10" /><Skeleton className="h-10" /></div>
        ) : failed ? (
          <EmptyState title="Patients could not be loaded" text="Check your connection and refresh." />
        ) : rows.length === 0 ? (
          <EmptyState icon={<Users className="w-5 h-5" strokeWidth={1.75} />} title="No patients yet"
            text={isAdmin ? 'Add the patients you want the agent to call.' : 'An administrator adds patients here.'} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="text-left text-[12px] text-stone-500 dark:text-stone-400 border-b border-stone-100 dark:border-white/10">
                  <th className="font-medium px-5 py-3">Patient</th>
                  <th className="font-medium px-3 py-3">Username</th>
                  <th className="font-medium px-3 py-3">Phone</th>
                  <th className="font-medium px-3 py-3">Language</th>
                  <th className="font-medium px-5 py-3">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100 dark:divide-white/5">
                {rows.map((p) => (
                  <tr key={p.id}>
                    <td className="px-5 py-3 font-medium text-stone-900 dark:text-white">{p.name}{p.guardian_name ? <span className="font-normal text-stone-500 dark:text-stone-400"> · with {p.guardian_name}</span> : null}</td>
                    <td className="px-3 py-3 text-stone-600 dark:text-stone-300">{p.username || '—'}</td>
                    <td className="px-3 py-3 tabular-nums text-stone-600 dark:text-stone-300">{p.phone || '—'}</td>
                    <td className="px-3 py-3 text-stone-600 dark:text-stone-300">{p.language === 'ar' ? 'Arabic' : 'English'}</td>
                    <td className="px-5 py-3">
                      {p.do_not_call ? <Badge tone="red">Do not call</Badge> : !p.has_dob ? <Badge tone="amber">No date of birth</Badge> : <Badge tone="green">Can be called</Badge>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Dialog open={open} onClose={() => !saving && setOpen(false)} title="Add a patient"
        description="The agent asks the person on the line for this date of birth before it says anything about the medicines."
        footer={(
          <>
            <Button onClick={() => setOpen(false)} disabled={saving}>Cancel</Button>
            <Button variant="primary" onClick={save} disabled={saving || !form.first_name.trim() || !form.phone_e164.trim() || !form.identity_dob.trim()}>Save</Button>
          </>
        )}>
        <div className="pb-3 grid grid-cols-2 gap-3">
          <FormField label="First name" required><input className={inputClass} value={form.first_name} onChange={set('first_name')} /></FormField>
          <FormField label="Last name"><input className={inputClass} value={form.last_name} onChange={set('last_name')} /></FormField>
          <FormField label="Username" hint="Optional, for example aadi.haldar"><input className={inputClass} value={form.username} onChange={set('username')} /></FormField>
          <FormField label="Language">
            <select className={inputClass} value={form.preferred_language} onChange={set('preferred_language')}><option value="en">English</option><option value="ar">Arabic</option></select>
          </FormField>
          <FormField label="Phone" required hint="With the country code"><input className={inputClass} value={form.phone_e164} onChange={set('phone_e164')} placeholder="+971 50 123 4567" inputMode="tel" /></FormField>
          <FormField label="Date of birth" required hint="For example 2001-02-03"><input className={inputClass} value={form.identity_dob} onChange={set('identity_dob')} placeholder="YYYY-MM-DD" /></FormField>
          {error && <p role="alert" className="col-span-2 text-[13px] text-rose-600 dark:text-rose-400">{error}</p>}
        </div>
      </Dialog>
    </Page>
  );
}
