/** The backend stores times as UTC without a zone marker ("2026-10-21 14:24:50"). Read them as UTC. */
export function parseUtc(iso?: string | null): Date | null {
  if (!iso) return null;
  const d = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso.replace(' ', 'T')}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "21 Oct, 6:24 pm" in the company's own time zone. */
export function formatWhen(iso?: string | null, zone = 'Asia/Kolkata'): string {
  const d = parseUtc(iso);
  if (!d) return '';
  return d.toLocaleString('en-GB', { timeZone: zone, day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit', hour12: true });
}

export function formatDay(iso?: string | null, zone = 'Asia/Kolkata'): string {
  const d = parseUtc(iso);
  return d ? d.toLocaleDateString('en-GB', { timeZone: zone, day: 'numeric', month: 'short', year: 'numeric' }) : '';
}

/** "in 2 h", "3 d ago", "now": how far a time is from now, in either direction. */
export function relativeWhen(iso?: string | null): string {
  const d = parseUtc(iso);
  if (!d) return '';
  const secs = (d.getTime() - Date.now()) / 1000;
  const a = Math.abs(secs);
  if (a < 60) return secs >= 0 ? 'now' : 'just now';
  const core = a < 5400 ? `${Math.round(a / 60)} min` : a < 129600 ? `${Math.round(a / 3600)} h` : `${Math.round(a / 86400)} d`;
  return secs >= 0 ? `in ${core}` : `${core} ago`;
}
