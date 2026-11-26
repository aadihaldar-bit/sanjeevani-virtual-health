'use client';

import React from 'react';
import { CheckCircle2, Circle, Loader2, MinusCircle, XCircle } from 'lucide-react';
import { stepsDone, type Step, type StepResult, type StepStatus } from '@/lib/architectRun';
import { ThinkingBlock, latestHeading } from './Thinking';
import { DocumentRow } from './KnowledgeList';

function Mark({ status }: { status: StepStatus }) {
  switch (status) {
    case 'running': return <Loader2 className="w-4 h-4 animate-spin text-indigo-600 dark:text-indigo-300" role="img" aria-label="Working on it" />;
    case 'done': return <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400" role="img" aria-label="Done" />;
    case 'failed': return <XCircle className="w-4 h-4 text-rose-600 dark:text-rose-400" role="img" aria-label="Did not work" />;
    case 'skipped': return <MinusCircle className="w-4 h-4 text-stone-400" role="img" aria-label="Skipped" />;
    default: return <Circle className="w-4 h-4 text-stone-300 dark:text-stone-600" role="img" aria-label="Waiting" />;
  }
}

const SUMMARY = 'cursor-pointer text-[11.5px] text-indigo-700 dark:text-indigo-300';
const PRE = 'mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-md border border-stone-200 dark:border-white/10 bg-white dark:bg-black/20 p-2 font-sans text-[12px] leading-relaxed text-stone-700 dark:text-stone-200';

function Result({ result }: { result: StepResult }) {
  switch (result.kind) {
    case 'look':
      return (
        <details className="mt-1">
          <summary className={SUMMARY}>What I found</summary>
          <div className="mt-1 space-y-1 text-[12px] text-stone-600 dark:text-stone-300">
            {result.agents.length > 0 && <p><span className="text-stone-400">Agents: </span>{result.agents.join(', ')}</p>}
            <p><span className="text-stone-400">Documents: </span>{result.documents.length ? result.documents.map((d) => d.replace(/\.md$/i, '')).join(', ') : 'none yet'}</p>
          </div>
        </details>
      );
    case 'design':
      return (
        <details className="mt-1">
          <summary className={SUMMARY}>See the design</summary>
          <div className="mt-1.5 space-y-2 text-[12px] text-stone-700 dark:text-stone-200">
            {result.summary && <p>{result.summary}</p>}
            <p><span className="text-stone-400">Callers first hear: </span>{result.greeting}</p>
            {result.desks.map((d) => (
              <div key={d.name} className="rounded-md bg-white dark:bg-white/5 border border-stone-200 dark:border-white/10 px-2.5 py-2">
                <p className="font-medium text-stone-900 dark:text-white">{d.name}</p>
                <p className="text-[11.5px] text-stone-500 dark:text-stone-400">{d.when}</p>
                <details className="mt-1"><summary className={SUMMARY}>What it is told</summary><pre className={PRE}>{d.prompt}</pre></details>
              </div>
            ))}
            {result.agent && <details><summary className={SUMMARY}>What the agent is told</summary><pre className={PRE}>{result.agent}</pre></details>}
            {result.documents.length > 0 && (
              <div>
                <p className="text-stone-400">Documents it reads</p>
                <ul className="mt-0.5 list-disc pl-4 space-y-0.5">
                  {result.documents.map((d) => <li key={d.name}>{d.name.replace(/\.md$/i, '')}{d.existing ? ' (already in your Knowledge)' : ` — ${d.about}`}</li>)}
                </ul>
              </div>
            )}
            <p><span className="text-stone-400">Voice: </span>{result.voice}{result.care ? ', gentle for patients' : ''}<span className="text-stone-400"> · Languages: </span>{result.languages.join(', ')}</p>
            {result.notes.length > 0 && <ul className="list-disc pl-4 space-y-0.5 text-stone-500 dark:text-stone-400">{result.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
          </div>
        </details>
      );
    case 'document':
      return (
        <details className="mt-1">
          <summary className={SUMMARY}>Read it{result.sample ? ' (sample details)' : ''}</summary>
          <pre className={PRE}>{result.content}</pre>
        </details>
      );
    case 'saved':
      return (
        <div className="mt-1.5 space-y-1.5">
          {result.documents.map((d) => <DocumentRow key={d.id} doc={d} />)}
        </div>
      );
    case 'agent':
      return (
        <div className="mt-1.5 space-y-1.5">
          {result.desks.length > 0 && <p className="text-[12px] text-stone-600 dark:text-stone-300">A front desk, and: {result.desks.join(', ')}.</p>}
          {result.documents.length > 0 && <p className="text-[12px] text-stone-600 dark:text-stone-300">Reads: {result.documents.map((d) => d.name.replace(/\.md$/i, '')).join(', ')}.</p>}
        </div>
      );
  }
}

/** The checklist: every item with where it stands, what it found, and what the model was thinking while it worked on it. */
export function Checklist({ steps }: { steps: Step[] }) {
  if (!steps.length) return null;
  return (
    <div className="rounded-xl border border-stone-200 dark:border-white/10 bg-white dark:bg-white/[0.03]">
      <div className="flex items-center justify-between px-3 py-2 border-b border-stone-200 dark:border-white/10">
        <p className="text-[11px] font-medium text-stone-400">Checklist</p>
        <p className="text-[11px] tabular-nums text-stone-400">{stepsDone(steps)} of {steps.length}</p>
      </div>
      <ol className="divide-y divide-stone-100 dark:divide-white/5">
        {steps.map((s) => {
          const heading = s.status === 'running' ? latestHeading(s.thinking) : '';
          return (
            <li key={s.id} className="px-3 py-2.5" data-step={s.id} data-status={s.status}>
              <div className="flex items-start gap-2.5">
                <span className="mt-0.5 flex-shrink-0"><Mark status={s.status} /></span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <p className={`text-[13px] font-medium break-words ${s.status === 'pending' ? 'text-stone-400' : s.status === 'skipped' ? 'text-stone-500 line-through decoration-stone-300' : 'text-stone-900 dark:text-white'}`}>{s.title}</p>
                    {s.secs !== undefined && s.status !== 'running' && <span className="text-[11px] tabular-nums text-stone-400 flex-shrink-0">{s.secs} s</span>}
                  </div>
                  {(heading || s.detail) && (
                    <p className={`text-[11.5px] break-words ${s.status === 'failed' ? 'text-rose-600 dark:text-rose-400' : 'text-stone-500 dark:text-stone-400'}`}>
                      {heading ? <span className="text-indigo-700 dark:text-indigo-300">{heading}…</span> : s.detail}
                    </p>
                  )}
                  {s.thinking.trim() && <div className="mt-1.5"><ThinkingBlock text={s.thinking} running={s.status === 'running'} /></div>}
                  {s.result && <Result result={s.result} />}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
