'use client';

import React from 'react';
import { X, ArrowRight, Bot, PhoneForwarded, BookOpen, Mic } from 'lucide-react';
import { useDashboardStore } from '@/lib/store';

export interface GuideContent {
  id: string;
  title: string;
  category: string;
  description: string;
  icon: React.ReactNode;
  actionText: string;
  actionType: 'tab' | 'url';
  actionTarget: string;
  steps: {
    title: string;
    description: string;
    code?: string;
  }[];
}

export const GUIDES_DATA: Record<string, GuideContent> = {
  'Configure Subagent Intent Routing': {
    id: 'subagent-routing',
    title: 'Configure Subagent Intent Routing',
    category: 'Multi-Agent State Machine',
    description: 'Learn how our autonomous multi-agent engine uses LLMs to evaluate caller intent at each conversational turn and dynamically hand off to specialized agents.',
    icon: <Bot className="w-5 h-5 text-stone-800 stroke-[1.5]" />,
    actionText: 'Open Visual Workflow Studio',
    actionType: 'url',
    actionTarget: '/workflow',
    steps: [
      {
        title: '1. Define Specialized Subagent Nodes',
        description: 'Create dedicated AgentNodes in the graph (e.g. General Receptionist, Technical Support Specialist, Warranty & AMC Agent, Escalation Manager). Each subagent carries its own custom system persona and voice.',
      },
      {
        title: '2. Connect Directional Graph Edges',
        description: 'Connect nodes with conditional transition rules such as "Customer reports hardware/paper jam" or "Customer inquires about warranty coverage".',
      },
      {
        title: '3. Autonomous Runtime Routing',
        description: 'The backend AgentRouter evaluates the caller utterance against all outgoing edge conditions and transfers conversation context seamlessly with memory preservation.',
        code: `POST /api/agents/{id}/chat\n{\n  "message": "My printer has error code 48",\n  "current_node_id": "agent-triage"\n}\n// Response: Routes to Technical Support (Deepak)`,
      },
    ],
  },
  'Add Live Telephony Transfer Node': {
    id: 'telephony-transfer',
    title: 'Add Live Telephony Transfer Node',
    category: 'Telephony & SIP Bridges',
    description: 'Route callers seamlessly from AI voice bots to human support supervisors via Twilio SIP Media Streams or carrier trunks.',
    icon: <PhoneForwarded className="w-5 h-5 text-stone-800 stroke-[1.5]" />,
    actionText: 'Configure in Workflow Studio',
    actionType: 'url',
    actionTarget: '/workflow',
    steps: [
      {
        title: '1. Add a Tool / Transfer Node',
        description: 'Click "+ Transfer Node" in the Workflow Studio toolbar. A specialized action node will appear on the canvas.',
      },
      {
        title: '2. Set the Destination Phone Number',
        description: 'Specify the supervisor or human branch number in E.164 format (e.g. +91 97643 67506).',
      },
      {
        title: '3. Connect Escalation Triggers',
        description: 'Connect an edge from your Escalation Agent to the Transfer Node with condition: "Customer demands human supervisor".',
        code: `{\n  "type": "toolNode",\n  "data": { "toolName": "Transfer Call (+91 97643 67506)" }\n}`,
      },
    ],
  },
  'Upload Knowledge Base for RAG': {
    id: 'kb-rag',
    title: 'Upload Knowledge Base for RAG',
    category: 'Grounded Retrieval Augmented Generation',
    description: 'Ground your voice agents in authoritative PDFs, service manuals, and product documents using dense embeddings and vector search.',
    icon: <BookOpen className="w-5 h-5 text-stone-800 stroke-[1.5]" />,
    actionText: 'Open Knowledge Base Manager',
    actionType: 'tab',
    actionTarget: 'knowledge',
    steps: [
      {
        title: '1. Upload Documentation Files',
        description: 'Go to the Knowledge Base panel and upload PDF, TXT, or DOCX files (e.g. Brother printer troubleshooting guide).',
      },
      {
        title: '2. Automatic Text Chunking & Vector Indexing',
        description: 'Documents are automatically segmented into semantic chunks and indexed into the vector collection for fast sub-50ms retrieval.',
      },
      {
        title: '3. Link Knowledge to Specific Subagents',
        description: 'In the Workflow Studio, click any Subagent Node and toggle "Enable RAG (Knowledge Base)" to ground that specific agent.',
      },
    ],
  },
  'Setup Multi-Voice Turn Synthesis': {
    id: 'multi-voice',
    title: 'Setup Multi-Voice Turn Synthesis',
    category: 'Gnani Timbre v2.5 Voice Engine',
    description: 'Assign unique lifelike synthetic voices to each subagent persona in your workflow across multiple Indian languages.',
    icon: <Mic className="w-5 h-5 text-stone-800 stroke-[1.5]" />,
    actionText: 'Try in Speech Playground',
    actionType: 'tab',
    actionTarget: 'speech-tts',
    steps: [
      {
        title: '1. Select Available Voice Personas',
        description: 'Choose from Gnani Timbre v2.5 voices including Aarohi (Female), Deepak (Male), Radhika (Female), Shweta (Female), Nalini (Female), and Hemraj (Male).',
      },
      {
        title: '2. Configure per Subagent Node',
        description: 'In the Workflow Studio sidebar, set General Triage to "Aarohi", Technical Support to "Deepak", and Escalations to "Radhika".',
      },
      {
        title: '3. Real-Time Streaming Audio Generation',
        description: 'During a live phone call, whenever the conversation transitions to a new subagent, the audio media bridge immediately switches voices seamlessly without hanging up.',
        code: `GnaniTTS.synthesize(text="Hello, I'm Deepak", speaker="Deepak", lang="en-IN")`,
      },
    ],
  },
};

export function QuickGuideModal({
  guideKey,
  guideId,
  onClose,
}: {
  guideKey?: string | null;
  guideId?: string | null;
  onClose: () => void;
}) {
  const { setSelectedTab } = useDashboardStore();
  const key = guideKey || guideId;
  const guide = key ? GUIDES_DATA[key] : null;

  if (!guide) return null;

  const handleAction = () => {
    onClose();
    if (guide.actionType === 'tab') {
      setSelectedTab(guide.actionTarget as any);
    } else if (guide.actionType === 'url') {
      window.location.href = guide.actionTarget;
    }
  };

  return (
    <div className="fixed inset-0 bg-stone-900/40 backdrop-blur-xs flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 rounded-2xl shadow-xl w-full max-w-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150">
        {/* Header */}
        <div className="p-5 border-b border-stone-200 dark:border-white/10 flex items-start justify-between bg-stone-50/70 dark:bg-stone-800/60">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-white dark:bg-stone-800 border border-stone-200 dark:border-white/10 flex items-center justify-center shadow-2xs">
              {guide.icon}
            </div>
            <div>
              <span className="text-[10px] font-bold uppercase tracking-wider text-stone-500">
                {guide.category}
              </span>
              <h2 className="text-base font-bold text-stone-900 dark:text-white">{guide.title}</h2>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-stone-400 hover:text-stone-700 hover:bg-stone-100 transition-colors"
          >
            <X className="w-5 h-5 stroke-[1.5]" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-5 max-h-[70vh] overflow-y-auto">
          <p className="text-xs text-stone-600 dark:text-stone-300 leading-relaxed">{guide.description}</p>

          <div className="space-y-4">
            {guide.steps.map((step, idx) => (
              <div key={idx} className="p-4 rounded-xl bg-stone-50/80 dark:bg-stone-800/80 border border-stone-200/80 dark:border-white/10">
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="w-5 h-5 rounded-full bg-stone-200 text-stone-800 flex items-center justify-center text-[10px] font-bold">
                    {idx + 1}
                  </span>
                  <h3 className="text-xs font-bold text-stone-900 dark:text-white">{step.title}</h3>
                </div>
                <p className="text-xs text-stone-600 dark:text-stone-300 leading-relaxed ml-7">{step.description}</p>
                {step.code && (
                  <pre className="mt-2.5 ml-7 p-3 rounded-lg bg-stone-900 text-stone-200 text-[11px] font-mono overflow-x-auto leading-relaxed">
                    {step.code}
                  </pre>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-stone-200 dark:border-white/10 bg-stone-50/70 dark:bg-stone-800/60 flex items-center justify-between">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-xl text-xs font-semibold text-stone-600 hover:text-stone-900 hover:bg-stone-100 transition-colors"
          >
            Close Guide
          </button>
          <button
            onClick={handleAction}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-stone-900 dark:bg-white hover:bg-stone-800 dark:hover:bg-stone-100 text-white dark:text-stone-900 text-xs font-semibold transition-all shadow-xs"
          >
            <span>{guide.actionText}</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
