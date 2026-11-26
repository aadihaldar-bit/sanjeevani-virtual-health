'use client';

import React from 'react';
import { ShieldCheck, ShieldAlert, ShieldX } from 'lucide-react';
import type { SecurityData } from '@/lib/api';

const ATTACK_LABELS: Record<string, string> = {
  roleplay_hijack: 'Roleplay hijack',
  direct_instruction_override: 'Instruction override',
  obfuscation_encoding: 'Obfuscation / encoding',
  other_policy_violation: 'Policy violation',
  unclassified: 'Unclassified',
};

export function attackLabel(type: string): string {
  return ATTACK_LABELS[type] ?? type.replace(/_/g, ' ');
}

export function SecurityBadge({ security }: { security?: SecurityData | null }) {
  if (!security || !security.guard_events || security.guard_events.length === 0) {
    return <span className="text-stone-400 text-xs">—</span>;
  }
  const level = security.max_threat_severity ?? 0;
  const blocked = security.jailbreak_attempts > 0;

  if (blocked) {
    return (
      <span
        title={`${security.jailbreak_attempts} blocked attempt(s): ${security.attack_types_seen.map(attackLabel).join(', ')}`}
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium text-red-800 dark:text-red-300 border border-red-200 dark:border-red-800/60 bg-red-50 dark:bg-red-950/50"
      >
        <ShieldX className="w-3 h-3" />
        Blocked
      </span>
    );
  }
  if (level >= 1) {
    return (
      <span
        title={security.attack_types_seen.map(attackLabel).join(', ') || 'Suspicious phrasing'}
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium text-amber-800 dark:text-amber-300 border border-amber-200 dark:border-amber-800/60 bg-amber-50 dark:bg-amber-950/50"
      >
        <ShieldAlert className="w-3 h-3" />
        Level {level}
      </span>
    );
  }
  return (
    <span
      title={`${security.guard_events.length} turn(s) checked, none flagged`}
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium text-emerald-800 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800/60 bg-emerald-50 dark:bg-emerald-950/50"
    >
      <ShieldCheck className="w-3 h-3" />
      Safe
    </span>
  );
}

export function SecurityPanel({ security }: { security?: SecurityData | null }) {
  if (!security || !security.guard_events || security.guard_events.length === 0) {
    return <p className="text-xs text-stone-400 italic">No guard data recorded for this call.</p>;
  }
  const flagged = security.guard_events.filter((e) => e.threat_severity >= 1 || e.is_jailbreak);
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 text-xs">
        <SecurityBadge security={security} />
        <span className="text-stone-500 dark:text-stone-400">
          {security.guard_events.length} turn(s) checked · max severity {security.max_threat_severity}/3 ·{' '}
          {security.jailbreak_attempts} blocked
        </span>
      </div>
      {flagged.length > 0 && (
        <ul className="space-y-1">
          {flagged.map((e) => (
            <li
              key={e.turn}
              className="text-[11px] text-stone-600 dark:text-stone-300 px-2 py-1 rounded bg-stone-50 dark:bg-white/5 border border-stone-200 dark:border-white/10"
            >
              Turn {e.turn}: <b>{attackLabel(e.attack_type)}</b> · severity {e.threat_severity}/3 · score{' '}
              {(e.jailbreak_probability * 100).toFixed(0)}%{e.is_jailbreak ? ' · blocked' : ''}
              <span className="text-stone-400"> ({e.guard_engine})</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
