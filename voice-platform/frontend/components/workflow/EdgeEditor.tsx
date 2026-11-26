'use client';

import React from 'react';
import { GitBranch, Trash2 } from 'lucide-react';
import { HELP, INPUT, LABEL, PANEL_HEAD } from './styles';

export default function EdgeEditor({
  edge,
  sourceName,
  targetName,
  onChange,
  onDelete,
}: {
  edge: any;
  sourceName: string;
  targetName: string;
  onChange: (id: string, patch: { label?: string; condition?: string }) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="flex flex-col h-full">
      <div className={PANEL_HEAD}>
        <h2 className="text-[14px] font-semibold text-stone-900 dark:text-white flex items-center gap-2">
          <GitBranch className="w-4 h-4 text-stone-400" strokeWidth={1.75} /> Path between steps
        </h2>
        <p className="text-[12px] text-stone-500 dark:text-stone-400 mt-1 truncate">
          {sourceName} <span aria-hidden>→</span> {targetName}
        </p>
      </div>

      <div className="p-4 space-y-4">
        <div>
          <label className={LABEL} htmlFor="edge-label">Name</label>
          <input id="edge-label" type="text" value={edge.label || ''} onChange={(e) => onChange(edge.id, { label: e.target.value })}
            className={INPUT} placeholder="e.g. Wants to book" />
          <p className={HELP}>Shown on the canvas so the flow is easy to read.</p>
        </div>

        <div>
          <label className={LABEL} htmlFor="edge-condition">When should the call take this path?</label>
          <textarea id="edge-condition" value={edge.condition || ''} onChange={(e) => onChange(edge.id, { condition: e.target.value })}
            rows={4} className={`${INPUT} resize-none`}
            placeholder="Describe it in plain words, e.g. The caller asks to book, change or cancel an appointment." />
          <p className={HELP}>The router reads this to decide when to pass the caller on. Leave it empty to rely on the name.</p>
        </div>

        <button onClick={() => onDelete(edge.id)}
          className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg text-[13px] text-rose-600 dark:text-rose-400 border border-rose-200 dark:border-rose-500/30 hover:bg-rose-50 dark:hover:bg-rose-500/10 cursor-pointer">
          <Trash2 className="w-3.5 h-3.5" /> Delete this path
        </button>
      </div>
    </div>
  );
}
