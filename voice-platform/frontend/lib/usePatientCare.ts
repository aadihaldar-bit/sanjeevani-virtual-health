'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/adminApi';

export interface PatientLabel { name: string; contact: string | null; phone: string | null; language: string | null; do_not_call: boolean; }

let zonePromise: Promise<string> | null = null;
/** The company's time zone, fetched once. Until it arrives (or if it cannot), India time. */
export function useCompanyZone(): string {
  const [zone, setZone] = useState('Asia/Kolkata');
  useEffect(() => {
    if (!zonePromise) zonePromise = api<{ timezone: string }>('/api/sanjeevani/settings').then((r) => r.timezone || 'Asia/Kolkata').catch(() => 'Asia/Kolkata');
    let cancelled = false;
    zonePromise.then((z) => { if (!cancelled) setZone(z); });
    return () => { cancelled = true; };
  }, []);
  return zone;
}

/** A list from the patient-care API: loading, failed, and a reload. */
export function useListing<T>(path: string, key: string) {
  const [rows, setRows] = useState<T[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setFailed(false);
    api<Record<string, T[]>>(path)
      .then((r) => { if (!cancelled) setRows(r[key] || []); })
      .catch(() => { if (!cancelled) { setRows([]); setFailed(true); } });
    return () => { cancelled = true; };
  }, [path, key, tick]);
  return { rows, failed, reload: () => setTick((t) => t + 1) };
}
