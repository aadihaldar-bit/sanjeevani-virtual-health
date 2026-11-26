import React from 'react';
import { Handle, Position } from '@xyflow/react';
import { BookOpen, Bot, Cpu, Mic, PhoneForwarded, PhoneOff, Play, Ticket } from 'lucide-react';

type Tone = 'indigo' | 'amber' | 'emerald' | 'stone';

const CHIP: Record<Tone, string> = {
  indigo: 'bg-indigo-50 text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-300',
  amber: 'bg-amber-50 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300',
  emerald: 'bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300',
  stone: 'bg-stone-100 text-stone-600 dark:bg-white/10 dark:text-stone-300',
};

const HANDLE = '!w-2.5 !h-2.5 !bg-stone-400 !border-2 !border-white dark:!border-[#0f121a]';

/** Tool steps either raise a ticket or hand the call to a person (the backend decides the same way from the name). */
export const isTicketTool = (data: any) => /ticket/i.test(String(data?.toolName || data?.tool_name || ''));

function Shell({ selected, tone, icon, title, tag, input = true, output = true, children }: {
  selected?: boolean; tone: Tone; icon: React.ReactNode; title: string; tag?: string;
  input?: boolean; output?: boolean; children?: React.ReactNode;
}) {
  return (
    <div className={`min-w-[240px] max-w-[280px] rounded-xl border bg-white dark:bg-[#0f121a] p-3.5 shadow-sm transition-colors ${
      selected ? 'border-indigo-500 ring-2 ring-indigo-500/20' : 'border-stone-200 dark:border-white/10 hover:border-stone-300 dark:hover:border-white/25'}`}>
      {input && <Handle type="target" position={Position.Left} isConnectable className={HANDLE} />}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2.5 min-w-0">
          <span className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 ${CHIP[tone]}`}>{icon}</span>
          <span className="text-[13px] font-semibold text-stone-900 dark:text-white truncate" title={title}>{title}</span>
        </div>
        {tag && <span className="text-[10px] font-medium tracking-wide px-1.5 py-0.5 rounded border border-stone-200 dark:border-white/10 text-stone-500 dark:text-stone-400">{tag}</span>}
      </div>
      {children}
      {output && <Handle type="source" position={Position.Right} isConnectable className={HANDLE} />}
    </div>
  );
}

export function StartNode({ selected }: any) {
  return (
    <Shell selected={selected} tone="stone" icon={<Play className="w-3.5 h-3.5" strokeWidth={1.75} />} title="Call starts" tag="START" input={false}>
      <p className="mt-2 text-[12px] leading-relaxed text-stone-500 dark:text-stone-400">Every call begins here and goes to the first agent.</p>
    </Shell>
  );
}

/** What an agent step is for on a call that uses several steps (the front desk hands the caller to a specialist). */
export const ROLE_TAGS: Record<string, string> = { orchestrator: 'FRONT DESK', specialist: 'SPECIALIST', outbound: 'WE CALL' };

export function AgentNode({ data, selected }: any) {
  const docs = Array.isArray(data.kb_docs) ? data.kb_docs.length : 0;
  return (
    <Shell selected={selected} tone="indigo" icon={<Bot className="w-3.5 h-3.5" strokeWidth={1.75} />} title={data.name || 'Agent'} tag={ROLE_TAGS[data.role]}>
      <p className="mt-2 mb-3 text-[12px] leading-relaxed text-stone-500 dark:text-stone-400 line-clamp-2">
        {data.system_prompt || 'No instructions yet. Select this step to write them.'}
      </p>
      <div className="flex flex-wrap gap-1.5 pt-2.5 border-t border-stone-100 dark:border-white/10">
        <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-md bg-stone-100 dark:bg-white/10 text-stone-600 dark:text-stone-300">
          <Mic className="w-3 h-3" strokeWidth={1.75} /> {data.voice || 'Default voice'}
        </span>
        <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-md bg-stone-100 dark:bg-white/10 text-stone-600 dark:text-stone-300">
          <Cpu className="w-3 h-3" strokeWidth={1.75} /> {data.llm_model || data.llm || 'Default model'}
        </span>
        {data.use_rag !== false && (
          <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-md bg-emerald-50 dark:bg-emerald-500/15 text-emerald-700 dark:text-emerald-300">
            <BookOpen className="w-3 h-3" strokeWidth={1.75} /> {docs > 0 ? `${docs} document${docs === 1 ? '' : 's'}` : 'Uses knowledge'}
          </span>
        )}
      </div>
    </Shell>
  );
}

export function ToolNode({ data, selected }: any) {
  const ticket = isTicketTool(data);
  const number = data.transfer_number || data.phone_number;
  return (
    <Shell selected={selected} tone={ticket ? 'emerald' : 'amber'}
      icon={ticket ? <Ticket className="w-3.5 h-3.5" strokeWidth={1.75} /> : <PhoneForwarded className="w-3.5 h-3.5" strokeWidth={1.75} />}
      title={data.name || (ticket ? 'Create a ticket' : 'Hand over to a person')} tag="ACTION">
      <p className="mt-2 text-[12px] leading-relaxed text-stone-500 dark:text-stone-400">
        {ticket ? 'Raises a ticket with what the caller reported.' : number ? `Transfers the call to ${number}.` : 'Transfers the call to your organization’s handover number.'}
      </p>
    </Shell>
  );
}

export function EndNode({ selected }: any) {
  return (
    <Shell selected={selected} tone="stone" icon={<PhoneOff className="w-3.5 h-3.5" strokeWidth={1.75} />} title="End the call" output={false}>
      <p className="mt-2 text-[12px] leading-relaxed text-stone-500 dark:text-stone-400">Says goodbye and hangs up.</p>
    </Shell>
  );
}

export const nodeTypes = {
  startNode: StartNode,
  agentNode: AgentNode,
  toolNode: ToolNode,
  endNode: EndNode,
};
