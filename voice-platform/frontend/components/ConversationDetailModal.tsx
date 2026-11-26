'use client';

import React, { useState, useRef, useEffect, useMemo } from 'react';
import {
  X, ChevronRight, Copy, Download, SkipBack, SkipForward, Play, Pause,
  Tag, User, Maximize2, ZoomIn, ZoomOut, ArrowRightLeft
} from 'lucide-react';

export interface ChatMessage {
  id: string;
  sender: 'user' | 'agent' | 'system';
  text: string;
  agentName?: string;
  voice?: string;
  toolCalled?: string | null;
  toolResult?: any;
  ragContext?: string;
  handoff?: boolean;
  timestamp: string;
  audioBase64?: string;
  responseMs?: number;
  llmMs?: number;
  ttsMs?: number;
  ragMs?: number;
  conversationSec?: number;
  isVoiceInput?: boolean;
}

export interface WorkflowOption {
  id: number;
  name: string;
  description: string;
  is_active: boolean;
}

interface Props {
  messages: ChatMessage[];
  sessionId: string;
  workflow: WorkflowOption | null;
  onClose: () => void;
  agentName: string;
}

function formatSec(sec: number) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

const CHUNK_IDS = ['J2SXYqGad0K6Xf7peOsx', 'tIMPGzGqaxusZXx01pkSz', 'M5uLtPyoAcIQ3kki8pL6', 'NOtlqY7DsOV9gcSct0Zd', 'BGwTYgndkptcvpPte7GP'];

export function ConversationDetailModal({ messages, sessionId, workflow, onClose, agentName }: Props) {
  const [activeTab, setActiveTab] = useState<'overview' | 'clientdata' | 'analysis' | 'workflow'>('overview');
  const [expandedRag, setExpandedRag] = useState<string | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playProgress, setPlayProgress] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const sentiment = useMemo(() => {
    const text = messages.map(m => m.text).join(' ').toLowerCase();
    const neg = ['urgent', 'issue', 'problem', 'error', 'broken', 'not working', 'fail', 'frustrated', 'down'].filter(w => text.includes(w)).length;
    const pos = ['thank', 'great', 'good', 'resolved', 'fixed', 'perfect', 'excellent'].filter(w => text.includes(w)).length;
    return { overall: pos > neg ? 'Positive' : neg > 2 ? 'Negative' : 'Neutral', frustration: neg > 2 ? 'High' : 'None' };
  }, [messages]);

  const summary = useMemo(() => {
    const userTexts = messages.filter(m => m.sender === 'user').map(m => m.text);
    if (!userTexts.length) return 'No conversation data available.';
    const first = userTexts[0] ? userTexts[0].substring(0, 100) : '';
    return `The conversation begins with the agent greeting the user and initiating a workflow. The user reports: "${first}". The agent assisted through the multi-agent pipeline, providing relevant responses from the knowledge base.`;
  }, [messages]);

  const totalDuration = useMemo(() => {
    const last = [...messages].reverse().find(m => m.conversationSec !== undefined);
    return last?.conversationSec || 42;
  }, [messages]);

  const agentTurns = messages.filter(m => m.sender === 'agent');
  const totalMessages = messages.filter(m => m.sender !== 'system').length;
  const hasRag = messages.some(m => m.ragContext);
  const avgLlm = Math.round(agentTurns.filter(m => m.llmMs).reduce((a, m) => a + (m.llmMs || 0), 0) / Math.max(1, agentTurns.filter(m => m.llmMs).length));
  const avgTts = Math.round(agentTurns.filter(m => m.ttsMs).reduce((a, m) => a + (m.ttsMs || 0), 0) / Math.max(1, agentTurns.filter(m => m.ttsMs).length));

  const waveData = useMemo(() =>
    Array.from({ length: 150 }, (_, i) => Math.abs(Math.sin(i * 0.31 + 1.2) * 38 + Math.sin(i * 0.73 + 0.5) * 28 + 32))
  , []);

  function togglePlay() {
    if (isPlaying) {
      if (timerRef.current) clearInterval(timerRef.current);
      setIsPlaying(false);
    } else {
      setIsPlaying(true);
      timerRef.current = setInterval(() => {
        setPlayProgress(prev => {
          if (prev >= 100) { clearInterval(timerRef.current!); setIsPlaying(false); return 0; }
          return prev + 0.5;
        });
      }, 200);
    }
  }

  useEffect(() => () => { if (timerRef.current) clearInterval(timerRef.current); }, []);

  function getUserMsgBefore(idx: number): string {
    for (let i = idx - 1; i >= 0; i--) {
      if (messages[i].sender === 'user') return messages[i].text;
    }
    return 'User query';
  }

  const tabs = [
    { id: 'overview' as const, label: 'Overview' },
    { id: 'clientdata' as const, label: 'Client data' },
    { id: 'analysis' as const, label: 'Analysis' },
    { id: 'workflow' as const, label: 'Workflow' },
  ];

  const currentTimeSec = Math.floor((playProgress / 100) * (totalDuration || 42));

  return (
    <div className="fixed inset-0 z-50 bg-white dark:bg-[#08090e] flex flex-col" style={{ fontFamily: 'system-ui,-apple-system,sans-serif' }}>

      {/* HEADER */}
      <div className="flex items-center gap-3 px-5 py-3 border-b border-stone-200 dark:border-white/10 flex-shrink-0 bg-white dark:bg-[#0d0f17] shadow-sm">
        <button onClick={onClose} className="p-1.5 hover:bg-stone-100 rounded cursor-pointer">
          <X className="w-4 h-4 text-stone-500 stroke-[1.5]" />
        </button>
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-stone-900 dark:text-white">Conversation with {agentName}</span>
          <span className="text-[10px] text-stone-400 border border-stone-200 px-1.5 py-0.5 rounded">&#x24D8; Main</span>
        </div>
        <div className="ml-auto flex items-center gap-1">
          <button className="p-1.5 hover:bg-stone-100 rounded cursor-pointer"><Maximize2 className="w-3.5 h-3.5 text-stone-400 stroke-[1.5]" /></button>
          <button onClick={onClose} className="p-1.5 hover:bg-stone-100 rounded cursor-pointer"><X className="w-3.5 h-3.5 text-stone-400 stroke-[1.5]" /></button>
        </div>
      </div>

      {/* BODY */}
      <div className="flex flex-1 overflow-hidden">

        {/* LEFT: Transcript */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="flex-1 overflow-y-auto px-8 py-6 space-y-6">

            {messages.map((msg, idx) => {
              if (msg.sender === 'system') return null;
              return (
                <div key={msg.id}>
                  {msg.handoff && (
                    <div className="flex items-center gap-2 my-4">
                      <div className="flex-1 h-px bg-stone-200" />
                      <div className="flex items-center gap-1.5 px-3 py-1.5 border border-stone-200 rounded-full bg-white text-[11px] text-stone-500 shadow-sm">
                        <ArrowRightLeft className="w-3 h-3 stroke-[1.5]" />
                        <span>Transitioned to {msg.agentName}</span>
                        <ChevronRight className="w-3 h-3" />
                        <span>Technical Support Agent</span>
                        <span className="text-[10px] border border-stone-200 px-1 rounded ml-1">&#x24D8; Main</span>
                      </div>
                      <div className="flex-1 h-px bg-stone-200" />
                    </div>
                  )}

                  {msg.sender === 'agent' ? (
                    <div className="flex gap-3">
                      <div className="w-8 h-8 rounded-full bg-teal-500 flex-shrink-0 flex items-center justify-center text-white text-xs font-bold mt-0.5 shadow-sm">
                        {(msg.agentName || agentName).substring(0, 1).toUpperCase()}
                      </div>
                      <div className="flex-1">
                        <div className="flex items-center gap-1.5 mb-1.5">
                          <span className="text-[11px] font-semibold text-stone-700">{msg.agentName || agentName}</span>
                          {msg.handoff && (<><ChevronRight className="w-3 h-3 text-stone-400" /><span className="text-[11px] text-stone-500">Technical Support Agent</span></>)}
                          <span className="text-[10px] border border-stone-200 px-1 rounded text-stone-400 ml-1">&#x24D8; Main</span>
                        </div>
                        <p className="text-sm text-stone-800 leading-relaxed">{msg.text}</p>
                        <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                          {msg.conversationSec !== undefined && (<span className="text-[10px] text-stone-400">{formatSec(msg.conversationSec)}</span>)}
                          {msg.llmMs && (
                            <button className="text-[10px] px-2 py-0.5 bg-stone-100 hover:bg-stone-200 rounded text-stone-600 font-medium cursor-pointer transition-colors">
                              LLM {msg.llmMs}ms &#9662;
                            </button>
                          )}
                          {msg.ttsMs && (
                            <button className="text-[10px] px-2 py-0.5 bg-stone-100 hover:bg-stone-200 rounded text-stone-600 font-medium cursor-pointer transition-colors">
                              TTS {msg.ttsMs}ms &#9662;
                            </button>
                          )}
                          {msg.ragContext && msg.ragMs && (
                            <button
                              onClick={() => setExpandedRag(expandedRag === msg.id ? null : msg.id)}
                              className={`text-[10px] px-2 py-0.5 rounded font-medium cursor-pointer transition-colors ${expandedRag === msg.id ? 'bg-stone-200 text-stone-800' : 'bg-stone-100 hover:bg-stone-200 text-stone-600'}`}
                            >
                              RAG {msg.ragMs}ms {expandedRag === msg.id ? '\u25b4' : '\u25be'}
                            </button>
                          )}
                          <button className="text-[10px] px-2 py-0.5 border border-stone-200 rounded text-stone-400 cursor-pointer">Version &#9662;</button>
                        </div>

                        {expandedRag === msg.id && msg.ragContext && (
                          <div className="mt-3 border border-stone-200 rounded-xl overflow-hidden text-[11px]">
                            <div className="flex items-center justify-between px-3 py-2 bg-stone-50 dark:bg-stone-800 border-b border-stone-200 dark:border-white/10 text-stone-800 dark:text-stone-200">
                              <span className="font-semibold text-stone-700">RAG Retrieval Information</span>
                              <button onClick={() => setExpandedRag(null)} className="cursor-pointer"><X className="w-3 h-3 text-stone-400" /></button>
                            </div>
                            <div className="p-3 space-y-3 bg-white dark:bg-stone-900">
                              <div>
                                <div className="text-[10px] font-semibold text-stone-400 uppercase tracking-wider mb-1">Query</div>
                                <p className="text-stone-700">{getUserMsgBefore(idx)}</p>
                              </div>
                              <div>
                                <div className="text-[10px] font-semibold text-stone-400 uppercase tracking-wider mb-2">Retrieved documents</div>
                                {CHUNK_IDS.map((chunkId, i) => (
                                  <div key={i} className="flex items-center justify-between py-2 border-t border-stone-100 first:border-0">
                                    <div>
                                      <div className="text-stone-700 font-medium">Brother International Customer Support Knowledge Base</div>
                                      <div className="text-[10px] text-stone-400">Chunk {chunkId}</div>
                                    </div>
                                    <button className="text-[10px] text-stone-500 border border-stone-200 px-2 py-0.5 rounded cursor-pointer hover:bg-stone-50 ml-4 flex-shrink-0">Show</button>
                                  </div>
                                ))}
                              </div>
                            </div>
                          </div>
                        )}
                        {msg.toolCalled && (
                          <div className="mt-2 text-[10px] text-stone-500">Tool: <code className="font-mono bg-stone-100 px-1 rounded">{msg.toolCalled}</code></div>
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className="flex gap-3">
                      <div className="w-8 h-8 rounded-full bg-stone-200 flex-shrink-0 flex items-center justify-center mt-0.5">
                        <User className="w-4 h-4 text-stone-500 stroke-[1.5]" />
                      </div>
                      <div className="flex-1">
                        <div className="flex items-center gap-2 mb-1.5"><span className="text-[11px] font-semibold text-stone-700">User</span></div>
                        <p className="text-sm text-stone-800 leading-relaxed">{msg.text}</p>
                        <div className="flex items-center gap-2 mt-2">
                          {msg.conversationSec !== undefined && (<span className="text-[10px] text-stone-400">{formatSec(msg.conversationSec)}</span>)}
                          <span className="text-[10px] px-1.5 py-0.5 bg-stone-100 rounded text-stone-500 font-medium">{msg.isVoiceInput ? `ASR ${20 + idx * 4}ms` : 'ASR 0ms'}</span>
                          <span className="text-[10px] text-stone-400">&#8226; +0:00</span>
                          <span className="text-[10px] text-stone-400 ml-auto cursor-pointer hover:text-stone-600">&#x24D8; Original transcript &#9662;</span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* AUDIO PLAYER */}
          <div className="border-t border-stone-200 dark:border-white/10 bg-white dark:bg-[#0d0f17] flex-shrink-0">
            <div className="flex items-center gap-2 px-5 py-2.5">
              <button className="p-1.5 hover:bg-stone-100 rounded cursor-pointer"><Download className="w-3.5 h-3.5 text-stone-500 stroke-[1.5]" /></button>
              <button className="text-[11px] text-stone-500 border border-stone-200 px-2 py-0.5 rounded cursor-pointer hover:bg-stone-50">1.0x</button>
              <button className="p-1.5 hover:bg-stone-100 rounded cursor-pointer"><SkipBack className="w-3.5 h-3.5 text-stone-500 stroke-[1.5]" /></button>
              <button onClick={togglePlay} className="w-8 h-8 bg-stone-900 hover:bg-stone-700 rounded-full flex items-center justify-center cursor-pointer transition-colors">
                {isPlaying ? <Pause className="w-3.5 h-3.5 text-white stroke-[1.5]" /> : <Play className="w-3.5 h-3.5 text-white stroke-[1.5] ml-0.5" />}
              </button>
              <button className="p-1.5 hover:bg-stone-100 rounded cursor-pointer"><SkipForward className="w-3.5 h-3.5 text-stone-500 stroke-[1.5]" /></button>
              <span className="text-[11px] text-stone-400">{formatSec(currentTimeSec)} / {formatSec(totalDuration || 42)}</span>
              <button className="ml-auto p-1.5 hover:bg-stone-100 rounded cursor-pointer"><Maximize2 className="w-3.5 h-3.5 text-stone-400 stroke-[1.5]" /></button>
            </div>
            <div className="px-5 pb-3">
              <div
                className="h-10 flex items-center gap-px cursor-pointer"
                onClick={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  setPlayProgress(Math.max(0, Math.min(100, ((e.clientX - rect.left) / rect.width) * 100)));
                }}
              >
                {waveData.map((h, i) => (
                  <div key={i} className="flex-1 rounded-sm transition-colors" style={{
                    height: `${Math.min(h, 90)}%`,
                    background: (i / waveData.length) * 100 < playProgress ? '#1c1917' : '#d6d3d1',
                    minWidth: '1px',
                  }} />
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* RIGHT: Tabs */}
        <div className="w-80 flex-shrink-0 flex flex-col border-l border-stone-200 dark:border-white/10 bg-white dark:bg-[#0d0f17]">
          <div className="flex border-b border-stone-200 dark:border-white/10">
            {tabs.map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex-1 py-3 text-[11px] font-medium cursor-pointer transition-colors border-b-2 ${ activeTab === tab.id ? 'text-stone-900 dark:text-white border-stone-900 dark:border-white' : 'text-stone-400 dark:text-stone-500 border-transparent hover:text-stone-600 dark:hover:text-stone-300' }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto">

            {activeTab === 'overview' && (
              <div className="p-5 space-y-5">
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-bold text-stone-800 dark:text-stone-200">Summary</span>
                    <button className="flex items-center gap-1 text-[10px] text-stone-400 border border-stone-200 px-2 py-0.5 rounded cursor-pointer hover:bg-stone-50"><Tag className="w-2.5 h-2.5" /> Add tag</button>
                  </div>
                  <p className="text-[11px] text-stone-600 dark:text-stone-300 leading-relaxed">{summary}</p>
                </div>
                <div className="h-px bg-stone-100" />
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-xs font-bold text-stone-800 dark:text-stone-200">Metadata</span>
                    <button className="flex items-center gap-1 text-[10px] text-stone-400 cursor-pointer hover:text-stone-600"><Copy className="w-2.5 h-2.5" /> Copy ID</button>
                  </div>
                  <table className="w-full">
                    <tbody>
                      {([
                        ['Conversation ID', 'conv_' + sessionId.substring(4, 28)],
                        ['Date', new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' })],
                        ['Agent', agentName + ' \u24d8 Main'],
                        ['Status', 'Successful'],
                        ['Sentiment', '\u2022 Sentiment ' + sentiment.overall + '   \u2022 Frustration ' + sentiment.frustration],
                        ['Environment', 'production'],
                        ['User ID', 'user_' + sessionId.substring(4, 20)],
                        ['Duration', (totalDuration || 42) + ' seconds'],
                        ['Messages', totalMessages + ' messages'],
                        ['Conversation cost', Math.max(50, totalMessages * 24) + ' credits'],
                        ['Credits (LLM)', String(Math.max(10, totalMessages * 2))],
                      ] as [string, string][]).map(([label, value]) => (
                        <tr key={label} className="text-[11px] align-top">
                          <td className="py-1.5 pr-2 text-stone-400 font-medium w-28 leading-tight">{label}</td>
                          <td className="py-1.5 leading-tight">
                            {label === 'Status' ? (
                              <span className="px-1.5 py-0.5 bg-emerald-50 text-emerald-700 border border-emerald-200 rounded text-[10px] font-semibold">{value}</span>
                            ) : (label === 'Conversation ID' || label === 'User ID') ? (
                              <span className="font-mono text-[10px] text-stone-600 break-all">{value}</span>
                            ) : (
                              <span className="text-stone-700 font-medium">{value}</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {activeTab === 'clientdata' && (
              <div className="p-5 flex items-center justify-center h-40"><p className="text-[11px] text-stone-400">No client data collected.</p></div>
            )}

            {activeTab === 'analysis' && (
              <div className="p-5 space-y-5">
                <div>
                  <span className="text-xs font-bold text-stone-800 dark:text-stone-200 block mb-3">Sentiment</span>
                  <div className="space-y-2">
                    {[['Overall', sentiment.overall], ['Frustration', sentiment.frustration]].map(([label, val]) => (
                      <div key={label} className="flex items-center gap-3">
                        <span className="text-[11px] text-stone-500 w-24">{label}</span>
                        <span className={`text-[10px] px-2 py-0.5 rounded font-semibold ${ val === 'Positive' ? 'bg-emerald-50 text-emerald-700' : val === 'Negative' || val === 'High' ? 'bg-red-50 text-red-700' : 'bg-stone-100 text-stone-600' }`}>{val}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="h-px bg-stone-100" />
                <div>
                  <span className="text-xs font-bold text-stone-800 dark:text-stone-200 block mb-2">Performance</span>
                  <div className="space-y-1.5 text-[11px]">
                    {[
                      ['Avg LLM latency', avgLlm ? avgLlm + 'ms' : '--'],
                      ['Avg TTS latency', avgTts ? avgTts + 'ms' : '--'],
                      ['Agent turns', String(agentTurns.length)],
                      ...(hasRag ? [['RAG turns', String(messages.filter(m => m.ragContext).length)]] : []),
                    ].map(([label, val]) => (
                      <div key={label} className="flex justify-between">
                        <span className="text-stone-500">{label}</span>
                        <span className="font-mono font-semibold text-stone-800">{val}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'workflow' && (
              <div className="p-4">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-xs font-bold text-stone-800 dark:text-stone-200">Agent Workflow</span>
                  <div className="flex gap-1">
                    <button className="p-1 hover:bg-stone-100 rounded cursor-pointer"><ZoomIn className="w-3 h-3 text-stone-400" /></button>
                    <button className="p-1 hover:bg-stone-100 rounded cursor-pointer"><ZoomOut className="w-3 h-3 text-stone-400" /></button>
                    <button className="p-1 hover:bg-stone-100 rounded cursor-pointer"><Maximize2 className="w-3 h-3 text-stone-400" /></button>
                  </div>
                </div>
                {workflow && <div className="text-[10px] text-stone-400 uppercase tracking-wider mb-3">Active: {workflow.name}</div>}
                <div className="bg-stone-50 dark:bg-stone-800/80 rounded-xl border border-stone-200 dark:border-white/10 p-4 overflow-auto">
                  <div className="flex flex-col items-stretch gap-0 text-[11px] min-w-[180px]">
                    {[
                      { name: 'Start', sub: '', type: 'start' },
                      { name: 'General Inquiry Agent', sub: 'Handles general customer enquiries', type: 'agent' },
                      { name: 'Technical Support Agent', sub: 'Handles technical issues and troubleshooting', type: 'agent' },
                      { name: 'Escalation Agent', sub: 'Handles escalations to human agents', type: 'agent' },
                      { name: 'Complaint Status Agent', sub: 'Handles complaint status queries', type: 'agent' },
                      { name: 'Transfer', sub: '', type: 'exit' },
                    ].map((node, i, arr) => (
                      <div key={node.name} className="flex flex-col items-center">
                        <div className={`w-full border rounded-lg px-3 py-2 shadow-sm ${ node.type === 'start' ? 'bg-stone-900 text-white border-stone-900' : node.type === 'exit' ? 'bg-stone-100 text-stone-500 border-stone-200' : 'bg-white dark:bg-stone-900 text-stone-700 dark:text-stone-200 border-stone-300 dark:border-white/10 hover:border-stone-400 dark:hover:border-white/20' } cursor-default transition-colors`}>
                          <div className="font-semibold">{node.name}</div>
                          {node.sub && <div className="text-[9px] mt-0.5 opacity-60 leading-tight">{node.sub}</div>}
                        </div>
                        {i < arr.length - 1 && (
                          <div className="flex flex-col items-center py-0.5">
                            <div className="w-px h-3 bg-stone-300" />
                            <div style={{ width: 0, height: 0, borderLeft: '4px solid transparent', borderRight: '4px solid transparent', borderTop: '5px solid #a8a29e' }} />
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}