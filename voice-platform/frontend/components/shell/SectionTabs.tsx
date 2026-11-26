'use client';

import React from 'react';
import type { SubTab } from './nav';
import type { TabId } from '@/lib/store';

interface Props {
  tabs: SubTab[];
  active: TabId;
  onSelect: (id: TabId) => void;
  /** page-level controls on the right (for example the agent filter on Calls) */
  right?: React.ReactNode;
}

/** The bar of sub-pages under the top bar. Shown only when a section has more than one, or has controls to offer. */
export function SectionTabs({ tabs, active, onSelect, right }: Props) {
  if (tabs.length < 2 && !right) return null;
  return (
    <div className="flex items-end justify-between gap-4 px-4 sm:px-6 border-b border-stone-200 dark:border-white/10 bg-white dark:bg-[#0c0e14]">
      <div role="tablist" className="flex gap-1 overflow-x-auto">
        {tabs.length > 1 && tabs.map((t) => {
          const on = t.id === active;
          return (
            <button
              key={t.id}
              role="tab"
              aria-selected={on}
              onClick={() => onSelect(t.id)}
              className={`relative h-11 px-3 text-[13px] font-medium whitespace-nowrap cursor-pointer transition-colors ${
                on ? 'text-stone-900 dark:text-white' : 'text-stone-500 dark:text-stone-400 hover:text-stone-800 dark:hover:text-stone-200'
              }`}
            >
              {t.label}
              {on && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-indigo-600 dark:bg-indigo-400" />}
            </button>
          );
        })}
      </div>
      {right && <div className="pb-2 flex items-center gap-2 flex-shrink-0">{right}</div>}
    </div>
  );
}
