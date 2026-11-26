'use client';

import React from 'react';
import { Cpu, Mic, Volume2 } from 'lucide-react';
import { Dialog } from '@/components/ui/Dialog';
import { BTN_PRIMARY, HELP, INPUT } from './styles';

export interface ProviderOption {
  id: string;
  label: string;
  available: boolean;
  healthy?: boolean | null;
  issue?: string | null;
  models?: string[];
  default_model?: string;
  voices?: { id: string; label: string }[];
}

/** Whether this agent's calls run on Gemini Live, which hears and speaks directly (no separate recognition or voice engine). */
export function runsOnGeminiLive(options: BuilderOptions | null, workflowId: number | null): boolean {
  const g = options?.gemini_live;
  return !!g && (g.for_all || (workflowId !== null && g.workflow_ids.includes(workflowId)));
}

export interface BuilderOptions {
  llm: ProviderOption[];
  stt: ProviderOption[];
  tts: ProviderOption[];
  languages: { id: string; label: string }[];
  tools: { id: string; label: string; fields: string[]; help: string }[];
  agent_tools?: { id: string; label: string; help: string }[];
  gemini_live?: {
    for_all: boolean; workflow_ids: number[]; default_model?: string;
    models?: { id: string; label: string }[]; thinking_levels?: string[];
    voices?: { id: string; label: string }[];
  };
  defaults: {
    llm: { provider: string };
    stt: { provider: string; language: string };
    tts: { provider: string };
  };
}

/** How an agent that runs on Gemini Live takes turns and which Gemini Live model it uses. Empty means the server defaults. */
export interface GeminiLiveChoices {
  model?: string;
  thinking_level?: string;
  turn_end?: 'quick' | 'balanced' | 'patient';
  turn_start?: 'sensitive' | 'balanced' | 'steady';
}

export interface WorkflowSettingsValue {
  gemini_live?: GeminiLiveChoices;
  llm?: { provider?: string; model?: string };
  stt?: { provider?: string; language?: string };
  tts?: { provider?: string };
}


function providerLabel(p: ProviderOption) {
  if (!p.available) return `${p.label} (not configured)`;
  if (p.healthy === false) return `${p.label} (not working: ${p.issue || 'failed a test request'})`;
  return p.label;
}

function providerUsable(p: ProviderOption) {
  return p.available && p.healthy !== false;
}

const SECTION = 'flex items-center gap-1.5 text-[13px] font-semibold text-stone-900 dark:text-white';

export default function WorkflowSettings({
  options,
  value,
  onChange,
  onClose,
  workflowId = null,
}: {
  options: BuilderOptions | null;
  value: WorkflowSettingsValue;
  onChange: (next: WorkflowSettingsValue) => void;
  onClose: () => void;
  workflowId?: number | null;
}) {
  const live = runsOnGeminiLive(options, workflowId);
  const choices: GeminiLiveChoices = value.gemini_live || {};
  const choose = (patch: Partial<GeminiLiveChoices>) => {
    const merged = { ...choices, ...patch } as Record<string, string | undefined>;
    Object.keys(merged).forEach((k) => { if (!merged[k]) delete merged[k]; });
    const next: WorkflowSettingsValue = { ...value, gemini_live: merged as GeminiLiveChoices };
    if (Object.keys(merged).length === 0) delete next.gemini_live;
    onChange(next);
  };
  const extended = (choices.model || '').includes('extended');
  const llmProvider = value.llm?.provider || '';
  const llmEntry = options?.llm.find((p) => p.id === llmProvider);

  const set = (kind: 'llm' | 'stt' | 'tts', patch: Record<string, string>) => {
    const merged = { ...(value[kind] || {}), ...patch } as Record<string, string>;
    Object.keys(merged).forEach((k) => {
      if (!merged[k]) delete merged[k];
    });
    const next: WorkflowSettingsValue = { ...value, [kind]: merged };
    if (Object.keys(merged).length === 0) delete (next as any)[kind];
    onChange(next);
  };

  return (
    <Dialog open title="Agent settings" onClose={onClose}
      description="Applies to every call this agent takes. Each step can still pick its own voice and model."
      footer={<button onClick={onClose} className={BTN_PRIMARY}>Done</button>}>
      {!options ? (
        <p className="py-6 text-[13px] text-stone-500 dark:text-stone-400">Loading available providers...</p>
      ) : (
        <div className="pb-3 space-y-6">
          {options.gemini_live && (
            <div className={`rounded-xl border p-3.5 text-[13px] leading-relaxed ${live
              ? 'border-emerald-200 dark:border-emerald-500/30 bg-emerald-50/70 dark:bg-emerald-500/10 text-emerald-900 dark:text-emerald-100'
              : 'border-stone-200 dark:border-white/10 bg-stone-50 dark:bg-white/5 text-stone-700 dark:text-stone-300'}`}>
              <p className="font-semibold">{live ? 'Calls run on Gemini Live' : 'Calls run on the standard pipeline'}</p>
              <p className="mt-0.5">
                {live
                  ? 'Gemini Live hears and speaks directly, in English and Arabic, with no separate speech recognition or voice engine. The three choices below are not used for this agent; they only apply if it is switched to the standard pipeline.'
                  : 'Speech recognition, the language model and the voice engine below are used for every call.'}
              </p>
            </div>
          )}
          {live && (
            <section className="space-y-3" aria-label="Gemini Live">
              <h3 className={SECTION}>Gemini Live</h3>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-[12px] font-medium text-stone-700 dark:text-stone-300 mb-1" htmlFor="gl-model">Model</label>
                  <select id="gl-model" className={INPUT} value={choices.model || ''} onChange={(e) => choose({ model: e.target.value, thinking_level: e.target.value ? choices.thinking_level : '' })}>
                    {(options.gemini_live?.models || [{ id: '', label: 'Standard (the server default)' }]).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-[12px] font-medium text-stone-700 dark:text-stone-300 mb-1" htmlFor="gl-think">Thinking</label>
                  <select id="gl-think" className={INPUT} value={choices.thinking_level || ''} disabled={!extended} onChange={(e) => choose({ thinking_level: e.target.value })}>
                    <option value="">{extended ? 'Low (fastest)' : 'Not used by this model'}</option>
                    {(options.gemini_live?.thinking_levels || []).map((l) => <option key={l} value={l}>{l.charAt(0) + l.slice(1).toLowerCase()}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-[12px] font-medium text-stone-700 dark:text-stone-300 mb-1" htmlFor="gl-end">When the caller has finished</label>
                  <select id="gl-end" className={INPUT} value={choices.turn_end || ''} onChange={(e) => choose({ turn_end: e.target.value as GeminiLiveChoices['turn_end'] })}>
                    <option value="">Server default</option>
                    <option value="quick">Quick: replies after a short pause</option>
                    <option value="balanced">Balanced</option>
                    <option value="patient">Patient: waits longer (dates, numbers, people who think aloud)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-[12px] font-medium text-stone-700 dark:text-stone-300 mb-1" htmlFor="gl-start">When the caller starts</label>
                  <select id="gl-start" className={INPUT} value={choices.turn_start || ''} onChange={(e) => choose({ turn_start: e.target.value as GeminiLiveChoices['turn_start'] })}>
                    <option value="">Server default</option>
                    <option value="sensitive">Sensitive: catches a quiet first word</option>
                    <option value="balanced">Balanced</option>
                    <option value="steady">Steady: ignores background noise</option>
                  </select>
                </div>
              </div>
              <p className={HELP}>Gemini Live listens for the caller itself. If callers get cut off mid-sentence choose Patient; if their first words go missing choose Sensitive; if the agent stops for background noise choose Steady. Changes apply to the next call.</p>
            </section>
          )}
          <div className={live ? 'space-y-6 opacity-50' : 'space-y-6'} aria-disabled={live}>
          <section className="space-y-2">
            <h3 className={SECTION}><Cpu className="w-4 h-4 text-stone-400" strokeWidth={1.75} /> Language model</h3>
            <div className="grid grid-cols-2 gap-2">
              <select className={INPUT} value={llmProvider} onChange={(e) => set('llm', { provider: e.target.value, model: '' })} aria-label="Language model provider">
                <option value="">Server default ({options.defaults.llm.provider})</option>
                {options.llm.map((p) => (
                  <option key={p.id} value={p.id} disabled={!providerUsable(p)}>{providerLabel(p)}</option>
                ))}
              </select>
              <select className={INPUT} value={value.llm?.model || ''} disabled={!llmEntry} onChange={(e) => set('llm', { model: e.target.value })} aria-label="Language model">
                <option value="">{llmEntry ? `Default (${llmEntry.default_model})` : 'Choose a provider first'}</option>
                {(llmEntry?.models || []).map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
          </section>

          <section className="space-y-2">
            <h3 className={SECTION}><Mic className="w-4 h-4 text-stone-400" strokeWidth={1.75} /> Speech recognition</h3>
            <div className="grid grid-cols-2 gap-2">
              <select className={INPUT} value={value.stt?.provider || ''} onChange={(e) => set('stt', { provider: e.target.value })} aria-label="Speech recognition provider">
                <option value="">Server default ({options.defaults.stt.provider})</option>
                {options.stt.map((p) => (
                  <option key={p.id} value={p.id} disabled={!p.available}>{providerLabel(p)}</option>
                ))}
              </select>
              <select className={INPUT} value={value.stt?.language || ''} onChange={(e) => set('stt', { language: e.target.value })} aria-label="Caller language">
                <option value="">Server default ({options.defaults.stt.language})</option>
                {options.languages.map((l) => (
                  <option key={l.id} value={l.id}>{l.label}</option>
                ))}
              </select>
            </div>
          </section>

          <section className="space-y-2">
            <h3 className={SECTION}><Volume2 className="w-4 h-4 text-stone-400" strokeWidth={1.75} /> Voice engine</h3>
            <select className={INPUT} value={value.tts?.provider || ''} onChange={(e) => set('tts', { provider: e.target.value })} aria-label="Voice engine provider">
              <option value="">Server default ({options.defaults.tts.provider})</option>
              {options.tts.map((p) => (
                <option key={p.id} value={p.id} disabled={!p.available}>{providerLabel(p)}</option>
              ))}
            </select>
            <p className={HELP}>The voice list in each step follows this engine. Providers that are not configured are greyed out.</p>
          </section>
          </div>
        </div>
      )}
    </Dialog>
  );
}
