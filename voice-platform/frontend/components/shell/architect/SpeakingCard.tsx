'use client';

import React from 'react';
import { ArrowUpRight, Check } from 'lucide-react';
import type { Proposal, Speaking } from '@/lib/architectRun';

export const CARD = 'rounded-xl border border-stone-200 dark:border-white/10 bg-stone-50 dark:bg-white/5 p-4';
export const BTN = 'h-9 px-3.5 rounded-lg bg-indigo-600 text-white text-[13px] font-medium hover:bg-indigo-500 disabled:opacity-60 cursor-pointer disabled:cursor-not-allowed';
export const BTN_QUIET = 'h-9 px-3 rounded-lg text-[13px] text-stone-500 hover:bg-stone-100 dark:hover:bg-white/10 cursor-pointer disabled:opacity-60';
export const BTN_OUTLINE = 'inline-flex items-center gap-1.5 h-9 px-3.5 rounded-lg border border-stone-300 dark:border-white/15 text-[13px] font-medium text-stone-800 dark:text-stone-100 hover:bg-stone-50 dark:hover:bg-white/10 cursor-pointer';

export type SpeakingProposal = Extract<Proposal, { type: 'change_speaking' }>;
export interface Outcome { state: 'creating' | 'created' | 'failed' | 'dismissed'; error?: string }

const SPEAKING_LABELS: Record<keyof Speaking, string> = {
  voice: 'Voice', model: 'Thinking', thinking_level: 'Thinking level', turn_end: 'When the caller has finished', turn_start: 'When the caller starts',
};
const WORDS: Record<string, string> = { standard: 'Standard', extended: 'Extended thinking' };
const sayChoice = (field: keyof Speaking, value: string | undefined): string => {
  if (!value) return field === 'voice' ? 'the default voice' : field === 'model' ? 'Standard' : 'the server default';
  return WORDS[value] ?? value.charAt(0).toUpperCase() + value.slice(1).toLowerCase();
};

/** A change to how an existing agent speaks: what it is now and what it will be, applied from the next call. It waits for the person to agree. */
export function SpeakingCard({ proposal, outcome, onApply, onDismiss }: {
  proposal: SpeakingProposal;
  outcome?: Outcome;
  onApply: () => void;
  onDismiss: () => void;
}) {
  const state = outcome?.state;
  const changed = Object.keys(proposal.changes) as (keyof Speaking)[];
  if (state === 'dismissed') return null;
  return (
    <div className={CARD}>
      <p className="text-[11px] font-medium text-stone-400">{state === 'created' ? 'Changed' : 'Ready to change'}</p>
      <p className="mt-0.5 text-[14px] font-semibold text-stone-900 dark:text-white">{proposal.agent_name}</p>
      <ul className="mt-2 space-y-1">
        {changed.map((field) => (
          <li key={field} className="text-[12.5px] text-stone-700 dark:text-stone-200">
            <span className="text-stone-500 dark:text-stone-400">{SPEAKING_LABELS[field]}: </span>
            {sayChoice(field, proposal.current[field])} <span aria-hidden="true">→</span><span className="sr-only"> becomes </span> <strong>{sayChoice(field, proposal.changes[field])}</strong>
          </li>
        ))}
      </ul>
      {state === 'created' ? (
        <div className="mt-3 space-y-2">
          <p className="flex items-center gap-1.5 text-[13px] text-emerald-700 dark:text-emerald-400"><Check className="w-4 h-4" /> Done. It speaks this way from the next call.</p>
          <a href={`/workflow?id=${proposal.agent_id}`} className={BTN_OUTLINE}>Open in the builder <ArrowUpRight className="w-3.5 h-3.5" /></a>
        </div>
      ) : (
        <>
          <p className="mt-2 text-[11.5px] text-stone-500 dark:text-stone-400">It applies to every step of the agent, from the next call. Calls already going on are not changed.</p>
          {state === 'failed' && <p role="alert" className="mt-2 text-[12px] text-rose-600 dark:text-rose-400">{outcome?.error}</p>}
          <div className="mt-3 flex gap-2">
            <button onClick={onApply} disabled={state === 'creating'} className={BTN}>
              {state === 'creating' ? 'Changing…' : state === 'failed' ? 'Try again' : 'Change it'}
            </button>
            <button onClick={onDismiss} disabled={state === 'creating'} className={BTN_QUIET}>Not now</button>
          </div>
        </>
      )}
    </div>
  );
}
