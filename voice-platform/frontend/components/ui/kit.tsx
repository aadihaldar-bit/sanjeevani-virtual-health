'use client';

/**
 * The shared building blocks every screen uses, so the console looks like one product: a scrolling page with a
 * heading, cards, stat tiles, badges, buttons and an empty state. Light and dark both come from these classes.
 */
import React from 'react';

const cx = (...parts: Array<string | false | null | undefined>) => parts.filter(Boolean).join(' ');

/** A scrolling page: heading, optional actions on the right, then the content. */
export function Page({ title, subtitle, actions, children, width = 'max-w-6xl' }: {
  title?: string;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  width?: string;
}) {
  return (
    <div className="flex-1 min-w-0 overflow-y-auto">
      <div className={cx('mx-auto px-4 sm:px-6 py-6 space-y-6', width)}>
        {(title || actions) && (
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              {title && <h2 className="text-xl font-semibold text-stone-900 dark:text-white tracking-tight">{title}</h2>}
              {subtitle && <p className="mt-1 text-[13px] text-stone-500 dark:text-stone-400">{subtitle}</p>}
            </div>
            {actions && <div className="flex items-center gap-2">{actions}</div>}
          </div>
        )}
        {children}
      </div>
    </div>
  );
}

export function Card({ title, action, children, className, padded = true }: {
  title?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <section className={cx('rounded-xl border border-stone-200 dark:border-white/10 bg-white dark:bg-[#0f121a]', className)}>
      {(title || action) && (
        <div className="flex items-center justify-between gap-3 px-5 pt-4">
          {title && <h3 className="text-[13px] font-semibold text-stone-900 dark:text-white">{title}</h3>}
          {action}
        </div>
      )}
      <div className={cx(padded && 'p-5', title && padded && 'pt-3')}>{children}</div>
    </section>
  );
}

export function StatCard({ label, value, hint, loading }: { label: string; value: React.ReactNode; hint?: string; loading?: boolean }) {
  return (
    <div className="rounded-xl border border-stone-200 dark:border-white/10 bg-white dark:bg-[#0f121a] px-5 py-4">
      <p className="text-[12px] text-stone-500 dark:text-stone-400">{label}</p>
      {loading
        ? <div className="mt-2 h-7 w-16 rounded bg-stone-100 dark:bg-white/10 animate-pulse" />
        : <p className="mt-1 text-2xl font-semibold tabular-nums text-stone-900 dark:text-white">{value}</p>}
      {hint && <p className="mt-0.5 text-[11px] text-stone-400">{hint}</p>}
    </div>
  );
}

const BADGE: Record<string, string> = {
  green: 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-300 dark:border-emerald-500/20',
  amber: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-500/10 dark:text-amber-300 dark:border-amber-500/20',
  red: 'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-500/10 dark:text-rose-300 dark:border-rose-500/20',
  blue: 'bg-indigo-50 text-indigo-700 border-indigo-200 dark:bg-indigo-500/10 dark:text-indigo-300 dark:border-indigo-500/20',
  grey: 'bg-stone-100 text-stone-600 border-stone-200 dark:bg-white/5 dark:text-stone-300 dark:border-white/10',
};

export function Badge({ tone = 'grey', children }: { tone?: keyof typeof BADGE; children: React.ReactNode }) {
  return <span className={cx('inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] font-medium whitespace-nowrap', BADGE[tone])}>{children}</span>;
}

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
const BUTTON: Record<Variant, string> = {
  primary: 'bg-indigo-600 text-white hover:bg-indigo-500 border-transparent',
  secondary: 'bg-white dark:bg-white/5 text-stone-800 dark:text-stone-100 border-stone-300 dark:border-white/15 hover:bg-stone-50 dark:hover:bg-white/10',
  ghost: 'bg-transparent text-stone-600 dark:text-stone-300 border-transparent hover:bg-stone-100 dark:hover:bg-white/10',
  danger: 'bg-white dark:bg-transparent text-rose-600 dark:text-rose-400 border-rose-200 dark:border-rose-500/30 hover:bg-rose-50 dark:hover:bg-rose-500/10',
};

export function Button({ variant = 'secondary', className, children, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      {...rest}
      className={cx(
        'inline-flex items-center justify-center gap-1.5 h-9 px-3.5 rounded-lg border text-[13px] font-medium transition-colors cursor-pointer',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed',
        BUTTON[variant], className,
      )}
    >
      {children}
    </button>
  );
}

export function EmptyState({ icon, title, text, action }: { icon?: React.ReactNode; title: string; text?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center text-center py-10 px-4">
      {icon && <div className="w-11 h-11 rounded-full bg-stone-100 dark:bg-white/5 flex items-center justify-center text-stone-400 mb-3">{icon}</div>}
      <p className="text-[14px] font-medium text-stone-800 dark:text-stone-100">{title}</p>
      {text && <p className="mt-1 max-w-sm text-[13px] text-stone-500 dark:text-stone-400 leading-relaxed">{text}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx('rounded bg-stone-100 dark:bg-white/10 animate-pulse', className)} />;
}

/** "3 min ago" style time for lists. */
export function timeAgo(iso?: string | null): string {
  if (!iso) return '';
  const t = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z').getTime();
  if (Number.isNaN(t)) return '';
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
