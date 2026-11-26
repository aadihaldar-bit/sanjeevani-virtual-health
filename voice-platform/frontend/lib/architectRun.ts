/**
 * What Architect's run sends (see backend/app/architect_run.py) and how the console turns it into what the person sees: the model's thinking,
 * a checklist, and each item ticking off. `applyEvent` is pure, so a run can be replayed from its events.
 */

export type StepStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped';

export interface DeskView { name: string; when: string; prompt: string }
export interface KnowledgeDoc {
  id: string; name: string; title: string; sections: number; size_kb: number; uploaded_at: string | null; built_in: boolean; read_by: string[];
}
export interface SavedDoc { id: string; name: string }

export type StepResult =
  | { kind: 'look'; agents: string[]; documents: string[] }
  | {
    kind: 'design'; summary: string; voice: string; care: boolean; languages: string[]; greeting: string; notes: string[];
    desks: DeskView[]; agent: string | null; documents: { name: string; about: string; existing: boolean }[];
  }
  | { kind: 'document'; name: string; content: string; sample: boolean }
  | { kind: 'saved'; documents: SavedDoc[] }
  | { kind: 'agent'; id: number; name: string; desks: string[]; voice?: string; documents: { name: string; written_by_architect: boolean }[] };

export interface Step {
  id: string;
  title: string;
  detail: string;
  status: StepStatus;
  secs?: number;
  result?: StepResult;
  /** summaries of the model's thinking while it worked on this item */
  thinking: string;
}

export interface Speaking { voice: string; model: string; thinking_level: string; turn_end: string; turn_start: string }
export type Proposal =
  | { type: 'change_speaking'; agent_id: number; agent_name: string; changes: Partial<Speaking>; current: Speaking }
  | { type: 'open_page'; page: string; tab: string; label: string }
  | { type: 'show_knowledge'; documents: KnowledgeDoc[] };

export interface RunEnd {
  ok: boolean;
  summary: string;
  note: string;
  retry?: boolean;
  fallback?: { name: string; prompt: string };
  agent_id?: number;
  documents?: SavedDoc[];
  notes?: string[];
}

export type RunEvent =
  | { t: 'thinking'; text: string; step?: string }
  | { t: 'reply'; text: string }
  | { t: 'plan'; steps: { id: string; title: string; detail?: string }[] }
  | { t: 'insert'; before: string; steps: { id: string; title: string; detail?: string }[] }
  | { t: 'step'; id: string; status: Exclude<StepStatus, 'pending'>; detail?: string; result?: StepResult; secs?: number }
  | ({ t: 'action'; action: Proposal })
  | ({ t: 'done' } & RunEnd)
  | { t: 'error'; message: string }
  | { t: 'ping' };

/** Everything one assistant turn shows. */
export interface RunView {
  startedAt: number;
  /** the first look at the request, before there is a checklist */
  thinking: string;
  thinkingSecs?: number;
  steps: Step[];
  action: Proposal | null;
  end?: RunEnd;
  error?: string;
  phase: 'thinking' | 'working' | 'finished' | 'failed' | 'stopped';
}

export const newRun = (now: number): RunView => ({ startedAt: now, thinking: '', steps: [], action: null, phase: 'thinking' });

const fresh = (s: { id: string; title: string; detail?: string }): Step => ({ id: s.id, title: s.title, detail: s.detail ?? '', status: 'pending', thinking: '' });
const append = (before: string, text: string) => (before ? `${before}\n\n${text}` : text);

/** The run after one more event. `now` is when it arrived (it times the first thinking). */
export function applyEvent(run: RunView, event: RunEvent, now: number): RunView {
  const mapStep = (id: string, fn: (s: Step) => Step): Step[] => run.steps.map((s) => (s.id === id ? fn(s) : s));
  const stoppedThinking: Partial<RunView> = run.thinkingSecs === undefined ? { thinkingSecs: Math.max(1, Math.round((now - run.startedAt) / 1000)) } : {};
  switch (event.t) {
    case 'thinking':
      return event.step
        ? { ...run, steps: mapStep(event.step, (s) => ({ ...s, thinking: append(s.thinking, event.text) })) }
        : { ...run, thinking: append(run.thinking, event.text) };
    case 'reply':
      return { ...run, ...stoppedThinking };
    case 'plan':
      return { ...run, ...stoppedThinking, phase: 'working', steps: event.steps.map(fresh) };
    case 'insert': {
      const at = run.steps.findIndex((s) => s.id === event.before);
      const added = event.steps.filter((n) => !run.steps.some((s) => s.id === n.id)).map(fresh);
      return { ...run, steps: at < 0 ? [...run.steps, ...added] : [...run.steps.slice(0, at), ...added, ...run.steps.slice(at)] };
    }
    case 'step':
      return {
        ...run,
        steps: mapStep(event.id, (s) => ({
          ...s, status: event.status, detail: event.detail ?? s.detail, result: event.result ?? s.result, secs: event.secs ?? s.secs,
        })),
      };
    case 'action':
      return { ...run, ...stoppedThinking, action: event.action };
    case 'done': {
      const { t: _t, ...end } = event;
      const steps = run.steps.map((s): Step => (s.status === 'pending' ? { ...s, status: 'skipped' } : s));          // what never started will not now
      return { ...run, ...stoppedThinking, steps, end, phase: end.ok ? 'finished' : 'failed' };
    }
    case 'error':
      return {
        ...run, ...stoppedThinking, phase: 'failed', error: event.message,
        steps: run.steps.map((s): Step => (s.status === 'running' ? { ...s, status: 'failed' } : s.status === 'pending' ? { ...s, status: 'skipped' } : s)),
      };
    default:
      return run;
  }
}

/** A run that ended some other way than the server finishing it: the person stopped it, or the connection broke. */
export function endRun(run: RunView, how: 'stopped' | 'lost'): RunView {
  if (run.phase === 'finished' || run.phase === 'failed') return run;
  const steps = run.steps.map((s) => (s.status === 'running' || s.status === 'pending' ? { ...s, status: (how === 'stopped' ? 'skipped' : 'failed') as StepStatus } : s));
  return how === 'stopped'
    ? { ...run, steps, phase: 'stopped', thinkingSecs: run.thinkingSecs ?? Math.max(1, Math.round((Date.now() - run.startedAt) / 1000)) }
    : { ...run, steps, phase: 'failed', error: 'The connection broke before I finished. Check what was made, then try again.' };
}

/** The server's lines, one JSON object each, as they arrive. Lines that are not JSON are skipped. */
export async function* readEvents(res: Response): AsyncGenerator<RunEvent> {
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let held = '';
  const parse = (line: string): RunEvent | null => {
    try {
      const value = JSON.parse(line);
      return value && typeof value.t === 'string' ? (value as RunEvent) : null;
    } catch { return null; }
  };
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      held += decoder.decode(value, { stream: true });
      let at: number;
      while ((at = held.indexOf('\n')) >= 0) {
        const line = held.slice(0, at).trim();
        held = held.slice(at + 1);
        const event = line ? parse(line) : null;
        if (event) yield event;
      }
    }
    const last = held.trim() ? parse(held.trim()) : null;
    if (last) yield last;
  } finally {
    reader.releaseLock();
  }
}

export const stepsDone = (steps: Step[]) => steps.filter((s) => s.status === 'done' || s.status === 'skipped').length;
