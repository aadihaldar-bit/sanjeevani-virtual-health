'use client';

import React, { useEffect, useRef } from 'react';
import { X } from 'lucide-react';

interface Props {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: React.ReactNode;
  /** buttons along the bottom */
  footer?: React.ReactNode;
  width?: string;
}

/** A centred dialog: closes on Escape or a click outside, keeps keyboard focus inside, and announces itself. */
export function Dialog({ open, title, description, onClose, children, footer, width = 'max-w-lg' }: Props) {
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const first = panel.current?.querySelector<HTMLElement>('input, textarea, select, button:not([data-close])');
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onClose(); return; }
      if (e.key !== 'Tab' || !panel.current) return;
      const items = panel.current.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])');
      if (items.length === 0) return;
      const firstItem = items[0], lastItem = items[items.length - 1];
      if (e.shiftKey && document.activeElement === firstItem) { e.preventDefault(); lastItem.focus(); }
      else if (!e.shiftKey && document.activeElement === lastItem) { e.preventDefault(); firstItem.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); previous?.focus?.(); };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-stone-900/50 backdrop-blur-[2px]" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`w-full ${width} max-h-[90vh] flex flex-col rounded-2xl bg-white dark:bg-[#12141c] border border-stone-200 dark:border-white/10 shadow-2xl`}
      >
        <div className="flex items-start justify-between gap-4 px-6 pt-5 pb-3">
          <div className="min-w-0">
            <h2 className="text-[16px] font-semibold text-stone-900 dark:text-white">{title}</h2>
            {description && <p className="mt-1 text-[13px] text-stone-500 dark:text-stone-400">{description}</p>}
          </div>
          <button data-close onClick={onClose} aria-label="Close" className="p-1.5 -mr-1.5 rounded-lg text-stone-400 hover:text-stone-700 dark:hover:text-white hover:bg-stone-100 dark:hover:bg-white/10 cursor-pointer">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="px-6 py-2 overflow-y-auto">{children}</div>
        {footer && <div className="px-6 py-4 mt-1 border-t border-stone-100 dark:border-white/10 flex items-center justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

const field = 'w-full min-h-[2.25rem] px-3 py-1.5 rounded-lg text-[13px] bg-white dark:bg-white/5 border border-stone-300 dark:border-white/15 text-stone-900 dark:text-white placeholder:text-stone-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500';

/** Label + control + optional hint, so every form in the console is laid out the same way. */
export function FormField({ label, hint, required, children }: { label: string; hint?: string; required?: boolean; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-[12px] font-medium text-stone-700 dark:text-stone-300 mb-1">{label}{required && <span className="text-rose-500"> *</span>}</span>
      {children}
      {hint && <span className="block text-[11px] text-stone-400 mt-1">{hint}</span>}
    </label>
  );
}

export const inputClass = field;
