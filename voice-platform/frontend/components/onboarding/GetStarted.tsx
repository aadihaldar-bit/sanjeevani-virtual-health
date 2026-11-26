'use client';

import React, { useEffect } from 'react';
import { ArrowRight, BarChart3, Check, FlaskConical, Hammer, Phone, Sparkles } from 'lucide-react';
import { useDashboardStore } from '@/lib/store';
import type { TabId } from '@/lib/store';
import { useShellStore } from '@/lib/shellStore';
import { useWorkspace } from '@/lib/useMe';
import { onboardingFlags, useOnboarding } from '@/lib/useOnboarding';
import type { Step } from '@/lib/useOnboarding';
import { Button, Card, Page } from '@/components/ui/kit';
import { Dialog } from '@/components/ui/Dialog';

/** Does what a step's button says: open Architect, the new-agent dialog, or a page. */
export function useStepRunner() {
  const { setSelectedTab } = useDashboardStore();
  const { openArchitect, openNewAgent } = useShellStore();
  return (go: Step['go']) => {
    if (go === 'architect') openArchitect();
    else if (go === 'new-agent') openNewAgent();
    else setSelectedTab(go as TabId);
  };
}

const HOW_IT_WORKS = [
  { icon: Hammer, title: 'Build', text: 'Create an agent from a template, or describe it to Architect. Add your documents so it answers from your own material.' },
  { icon: FlaskConical, title: 'Test', text: 'Talk to your agent in the simulator. Change its instructions in the builder until it sounds right.' },
  { icon: Phone, title: 'Go live', text: 'Connect a phone number and make the agent live. From then on it answers real calls.' },
  { icon: BarChart3, title: 'Watch and improve', text: 'Read transcripts, see what callers ask, check tickets, and refine the agent from what you learn.' },
];

/** The guided start for a new company: what to do, in order, with a button for each, and a plain explanation of the platform. */
export function GetStarted() {
  const { workspaceName, isPlatform, canManage } = useWorkspace();
  const { steps, doneCount, total, next, complete, loading } = useOnboarding();
  const run = useStepRunner();
  const { openArchitect } = useShellStore();
  const pct = total ? Math.round((doneCount / total) * 100) : 0;

  return (
    <Page width="max-w-4xl" title="Get started" subtitle={isPlatform ? 'A short tour of how the platform works.' : `Set up ${workspaceName} in a few steps. We will point you to the next one.`}>
      {steps.length > 0 && (
        <Card>
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-[14px] font-semibold text-stone-900 dark:text-white">
                {complete ? 'You are all set' : `${doneCount} of ${total} steps done`}
              </p>
              <p className="text-[13px] text-stone-500 dark:text-stone-400 mt-0.5">
                {complete ? 'Your agent is live. Keep an eye on Calls and Analytics to see how it does.' : next ? `Next: ${next.title.toLowerCase()}.` : ''}
              </p>
            </div>
            {next && <Button variant="primary" onClick={() => run(next.go)}>{next.cta} <ArrowRight className="w-4 h-4" /></Button>}
          </div>
          <div className="mt-4 h-2 rounded-full bg-stone-100 dark:bg-white/10 overflow-hidden" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Setup progress">
            <div className="h-full rounded-full bg-indigo-600 transition-all duration-500" style={{ width: `${pct}%` }} />
          </div>
        </Card>
      )}

      {steps.length > 0 && (
        <ol className="space-y-3">
          {steps.map((s, i) => {
            const isNext = next?.id === s.id;
            return (
              <li key={s.id} className={`rounded-xl border p-5 flex items-start gap-4 bg-white dark:bg-[#0f121a] ${isNext ? 'border-indigo-300 dark:border-indigo-400/40 ring-1 ring-indigo-200 dark:ring-indigo-400/20' : 'border-stone-200 dark:border-white/10'}`}>
                <span className={`mt-0.5 w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 text-[12px] font-semibold ${s.done ? 'bg-emerald-500 text-white' : isNext ? 'bg-indigo-600 text-white' : 'border border-stone-300 dark:border-white/20 text-stone-500'}`}>
                  {s.done ? <Check className="w-4 h-4" strokeWidth={3} /> : i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className={`text-[14px] font-semibold ${s.done ? 'text-stone-400 line-through' : 'text-stone-900 dark:text-white'}`}>{s.title}</p>
                  <p className="mt-1 text-[13px] leading-relaxed text-stone-500 dark:text-stone-400">{s.why}</p>
                </div>
                {!s.done && <Button variant={isNext ? 'primary' : 'secondary'} className="flex-shrink-0" onClick={() => run(s.go)}>{s.cta}</Button>}
              </li>
            );
          })}
        </ol>
      )}

      {steps.length === 0 && !loading && !isPlatform && !canManage && (
        <Card>
          <p className="text-[14px] font-semibold text-stone-900 dark:text-white">What you can do here</p>
          <p className="mt-1 text-[13px] leading-relaxed text-stone-500 dark:text-stone-400">
            You can follow calls as they happen, read transcripts, work tickets and try agents in the simulator. Your administrator builds and changes the agents. Ask them if something you need is missing.
          </p>
        </Card>
      )}

      <div>
        <h3 className="text-[14px] font-semibold text-stone-900 dark:text-white mb-3">How the platform works</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {HOW_IT_WORKS.map(({ icon: Icon, title, text }, i) => (
            <div key={title} className="rounded-xl border border-stone-200 dark:border-white/10 bg-white dark:bg-[#0f121a] p-5">
              <div className="flex items-center gap-3">
                <span className="w-9 h-9 rounded-lg bg-indigo-50 dark:bg-indigo-500/15 text-indigo-600 dark:text-indigo-300 flex items-center justify-center"><Icon className="w-[18px] h-[18px]" strokeWidth={1.75} /></span>
                <p className="text-[14px] font-semibold text-stone-900 dark:text-white"><span className="text-stone-400 font-normal mr-1.5">{i + 1}</span>{title}</p>
              </div>
              <p className="mt-2.5 text-[13px] leading-relaxed text-stone-500 dark:text-stone-400">{text}</p>
            </div>
          ))}
        </div>
      </div>

      {canManage && (
        <Card>
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <span className="w-9 h-9 rounded-lg bg-indigo-50 dark:bg-indigo-500/15 text-indigo-600 dark:text-indigo-300 flex items-center justify-center flex-shrink-0"><Sparkles className="w-[18px] h-[18px]" /></span>
              <div>
                <p className="text-[14px] font-semibold text-stone-900 dark:text-white">Not sure where to start?</p>
                <p className="text-[13px] text-stone-500 dark:text-stone-400">Tell Architect what your business does and what the agent should handle. It sets up the first version for you.</p>
              </div>
            </div>
            <Button variant="primary" onClick={openArchitect} className="flex-shrink-0"><Sparkles className="w-4 h-4" /> Build with Architect</Button>
          </div>
        </Card>
      )}
    </Page>
  );
}

/** Shown once, the first time someone opens the console, so nobody lands on a screen with no idea what to do. */
export function WelcomeDialog() {
  const { welcomeOpen, setWelcome, openArchitect, openNewAgent } = useShellStore();
  const { setSelectedTab } = useDashboardStore();
  const { me, isPlatform, canManage, workspaceName } = useWorkspace();

  useEffect(() => {
    if (me && !isPlatform && !onboardingFlags.welcomed()) setWelcome(true);
  }, [me, isPlatform, setWelcome]);

  const close = () => { onboardingFlags.markWelcomed(); setWelcome(false); };
  const choose = (fn: () => void) => () => { close(); fn(); };

  return (
    <Dialog
      open={welcomeOpen}
      title={`Welcome to ${workspaceName}`}
      description={canManage
        ? 'Here you build voice agents that answer your phone calls. A good first step is to create one. You can change anything later.'
        : 'Here you can follow calls, read transcripts, work tickets and try your organization’s agents.'}
      onClose={close}
      width="max-w-2xl"
      footer={<Button variant="ghost" onClick={choose(() => setSelectedTab('get-started'))}>See the full guide</Button>}
    >
      {canManage ? (
        <div className="space-y-3 pb-2">
          <button onClick={choose(openArchitect)} className="w-full text-left rounded-xl border border-indigo-200 dark:border-indigo-400/30 bg-indigo-50/60 dark:bg-indigo-500/10 p-4 hover:bg-indigo-50 dark:hover:bg-indigo-500/15 cursor-pointer">
            <span className="flex items-center gap-2 text-[14px] font-semibold text-indigo-900 dark:text-indigo-100"><Sparkles className="w-4 h-4" /> Build with Architect <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-indigo-600 text-white">Recommended</span></span>
            <span className="block mt-1 text-[13px] text-indigo-900/70 dark:text-indigo-100/70">Describe your business and what the agent should do. Architect creates the first version.</span>
          </button>
          <button onClick={choose(() => openNewAgent())} className="w-full text-left rounded-xl border border-stone-200 dark:border-white/10 p-4 hover:bg-stone-50 dark:hover:bg-white/5 cursor-pointer">
            <span className="block text-[14px] font-semibold text-stone-900 dark:text-white">Start from a ready-made workflow</span>
            <span className="block mt-1 text-[13px] text-stone-500 dark:text-stone-400">Customer support, bookings, sales enquiries, payment reminders and more.</span>
          </button>
          <button onClick={choose(() => setSelectedTab('get-started'))} className="w-full text-left rounded-xl border border-stone-200 dark:border-white/10 p-4 hover:bg-stone-50 dark:hover:bg-white/5 cursor-pointer">
            <span className="block text-[14px] font-semibold text-stone-900 dark:text-white">Show me around</span>
            <span className="block mt-1 text-[13px] text-stone-500 dark:text-stone-400">A short checklist of the steps from first agent to live calls.</span>
          </button>
        </div>
      ) : (
        <p className="pb-3 text-[13px] leading-relaxed text-stone-600 dark:text-stone-300">
          Use <b>Calls</b> to follow live conversations and read transcripts, <b>Records</b> for tickets, and <b>Tools</b> to try an agent. Your administrator builds and changes the agents.
        </p>
      )}
    </Dialog>
  );
}
