'use client';

import React, { useEffect, useRef, useState } from 'react';
import { ChevronRight } from 'lucide-react';

/** The model's summaries of its thinking arrive as "**Heading**" then a paragraph. Only that bold is understood; everything else is plain text. */
function inline(text: string): React.ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) => (part.startsWith('**') && part.endsWith('**') && part.length > 4
    ? <strong key={i} className="font-semibold">{part.slice(2, -2)}</strong>
    : <React.Fragment key={i}>{part}</React.Fragment>));
}

function Thought({ text }: { text: string }) {
  return (
    <>
      {text.split(/\n{2,}/).map((para, i) => {
        const heading = /^\*\*([^*]+)\*\*$/.exec(para.trim());
        return heading
          ? <p key={i} className="mt-2 first:mt-0 text-[12px] font-semibold text-stone-600 dark:text-stone-300">{heading[1]}</p>
          : <p key={i} className="mt-0.5 text-[12px] leading-relaxed text-stone-500 dark:text-stone-400 whitespace-pre-line">{inline(para)}</p>;
      })}
    </>
  );
}

/** The heading of the latest summary: what the model is working on right now. */
export function latestHeading(text: string): string {
  const all = [...text.matchAll(/\*\*([^*]+)\*\*/g)];
  return all.length ? all[all.length - 1][1] : '';
}

/**
 * The model thinking out loud. Open and moving while it works; folded to "Thought for 8 s" when it has finished, and a click opens it again.
 * `running` is whether more may still arrive.
 */
export function ThinkingBlock({ text, running, secs, label = 'Thinking' }: { text: string; running: boolean; secs?: number; label?: string }) {
  const [open, setOpen] = useState(running);
  const body = useRef<HTMLDivElement>(null);
  const wasRunning = useRef(running);

  useEffect(() => {                                   // folds itself once, when the thinking ends
    if (wasRunning.current && !running) setOpen(false);
    wasRunning.current = running;
  }, [running]);

  useEffect(() => {                                   // follows the newest line while it is being written
    if (running && open && body.current) body.current.scrollTop = body.current.scrollHeight;
  }, [text, running, open]);

  if (!text.trim() && !running) return null;
  const title = running ? `${label}…` : secs ? `${label === 'Thinking' ? 'Thought' : label} for ${secs} s` : label;
  return (
    <div className="rounded-lg border border-stone-200 dark:border-white/10 bg-stone-50/70 dark:bg-white/[0.03]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        disabled={!text.trim()}
        className="w-full flex items-center gap-1.5 px-2.5 py-1.5 text-left text-[12px] font-medium text-stone-500 dark:text-stone-400 cursor-pointer disabled:cursor-default"
      >
        <ChevronRight className={`w-3.5 h-3.5 flex-shrink-0 transition-transform ${open ? 'rotate-90' : ''} ${text.trim() ? '' : 'opacity-0'}`} aria-hidden="true" />
        <span className={running ? 'animate-pulse' : ''}>{title}</span>
      </button>
      {open && text.trim() && (
        <div ref={body} aria-live="off" className="px-3 pb-2.5 pl-7 max-h-52 overflow-y-auto border-t border-stone-200/70 dark:border-white/5 pt-1.5">
          <Thought text={text} />
        </div>
      )}
    </div>
  );
}
