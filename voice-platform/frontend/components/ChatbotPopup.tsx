'use client';

import React, { useState, useEffect, useRef } from 'react';
import {
  Send, RefreshCw, Volume2, VolumeX, Mic, MicOff,
  X, Wrench, ChevronRight
} from 'lucide-react';
import { authFetch } from '@/lib/authFetch';
import { getApiBase } from '@/lib/api';
import { useShellStore } from '@/lib/shellStore';
import { useWorkspace } from '@/lib/useMe';

export interface PopupMessage {
  id: string;
  sender: 'user' | 'agent' | 'system';
  text: string;
  agentName?: string;
  toolCalled?: string | null;
  toolResult?: any;
  timestamp: string;
}

const QUICK_PROMPTS = [
  'Paper jam in Brother printer',
  'Wi-Fi setup instructions',
  'Raise high priority incident ticket',
  'Check printer warranty status',
];

export function ChatbotPopup() {
  const { architectOpen } = useShellStore();
  const { canManage } = useWorkspace();
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState<PopupMessage[]>([
    {
      id: 'init-1',
      sender: 'agent',
      agentName: 'STL Assistant',
      text: 'Hello! I am your STL Voice & Support Assistant. Ask me anything about Brother printers, technical troubleshooting, or raising Symphony incident tickets.',
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    },
  ]);
  const [inputText, setInputText] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [voiceEnabled, setVoiceEnabled] = useState(true);
  const [isListening, setIsListening] = useState(false);
  const [currentNodeId, setCurrentNodeId] = useState<string | null>(null);

  const sessionIdRef = useRef<string>(`popup-session-${Date.now()}`);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const recognitionRef = useRef<any>(null);

  // Auto-scroll to bottom of messages
  useEffect(() => {
    if (isOpen) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, isOpen]);

  // Speech Recognition initialization
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const SpeechRecognition =
        (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      if (SpeechRecognition) {
        const recognition = new SpeechRecognition();
        recognition.continuous = false;
        recognition.interimResults = false;
        recognition.lang = 'en-US';

        recognition.onresult = (event: any) => {
          const transcript = event.results[0][0].transcript;
          if (transcript) {
            setInputText(transcript);
            handleSendMessage(transcript);
          }
          setIsListening(false);
        };

        recognition.onerror = () => {
          setIsListening(false);
        };

        recognition.onend = () => {
          setIsListening(false);
        };

        recognitionRef.current = recognition;
      }
    }
  }, []);

  const speakText = (text: string) => {
    if (!voiceEnabled || typeof window === 'undefined' || !window.speechSynthesis) return;
    try {
      window.speechSynthesis.cancel();
      const cleanText = text.replace(/[*_#`]/g, '').trim();
      const utterance = new SpeechSynthesisUtterance(cleanText);
      utterance.rate = 1.0;
      utterance.pitch = 1.0;
      window.speechSynthesis.speak(utterance);
    } catch {
      // Ignore speech synthesis failures
    }
  };

  const toggleMic = () => {
    if (!recognitionRef.current) return;
    if (isListening) {
      recognitionRef.current.stop();
      setIsListening(false);
    } else {
      try {
        recognitionRef.current.start();
        setIsListening(true);
      } catch {
        setIsListening(false);
      }
    }
  };

  const handleSendMessage = async (textToSend?: string) => {
    const text = (textToSend || inputText).trim();
    if (!text || isLoading) return;

    const userMsg: PopupMessage = {
      id: `user-${Date.now()}`,
      sender: 'user',
      text,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInputText('');
    setIsLoading(true);

    try {
      const historyPayload = messages
        .filter((m) => m.sender !== 'system')
        .map((m) => ({
          role: m.sender === 'user' ? 'user' : 'assistant',
          content: m.text,
        }));

      const apiBase = getApiBase();
      const res = await authFetch(`${apiBase}/api/simulator/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionIdRef.current,
          user_message: text,
          history: historyPayload,
          current_node_id: currentNodeId,
        }),
      });

      if (!res.ok) {
        throw new Error(`Server returned HTTP ${res.status}`);
      }

      const data = await res.json();
      if (data?.next_node_id) {
        setCurrentNodeId(data.next_node_id);
      }
      const agentReplyText =
        data?.response ||
        data?.text ||
        'I processed your request, but received an empty reply from the agent graph.';

      const agentMsg: PopupMessage = {
        id: `agent-${Date.now()}`,
        sender: 'agent',
        text: agentReplyText,
        agentName: data?.agent_name || data?.agent || 'Support Agent',
        toolCalled: data?.tool_called || data?.tool || null,
        toolResult: data?.tool_result || null,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };

      setMessages((prev) => [...prev, agentMsg]);
      speakText(agentReplyText);
    } catch (err: any) {
      const errorMsg: PopupMessage = {
        id: `err-${Date.now()}`,
        sender: 'system',
        text: `Error connecting to assistant: ${err.message || 'Network error'}. Please retry.`,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleReset = () => {
    sessionIdRef.current = `popup-session-${Date.now()}`;
    setCurrentNodeId(null);
    if (typeof window !== 'undefined' && window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
    setMessages([
      {
        id: `init-${Date.now()}`,
        sender: 'agent',
        agentName: 'STL Assistant',
        text: 'Chat reset. How may I assist you with your Brother devices or IT tickets?',
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      },
    ]);
  };

  // Architect's panel takes the right edge of the screen, where this button and card float (they would cover its send button).
  // While it is open they step aside; the chat in them is kept and comes back when it closes.
  if (architectOpen && canManage) return null;

  return (
    <>
      {/* ─── Floating Pop-up Card ──────────────────────────────── */}
      {isOpen && (
        <div className="fixed bottom-20 right-6 z-50 w-[380px] max-w-[calc(100vw-2rem)] h-[520px] max-h-[calc(100vh-6.5rem)] flex flex-col rounded-2xl shadow-2xl border bg-white/95 text-stone-900 border-stone-200/90 dark:bg-[#0d0f17]/95 dark:text-stone-100 dark:border-white/10 backdrop-blur-md overflow-hidden transition-all animate-in fade-in slide-in-from-bottom-3 duration-200">
          
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-stone-200/80 dark:border-white/10 bg-stone-50/80 dark:bg-stone-900/80">
            <div className="flex items-center gap-2.5">
              {/* Gradient 4-point star avatar */}
              <div className="w-7 h-7 rounded-full bg-gradient-to-tr from-amber-400 via-orange-500 to-indigo-500 flex items-center justify-center shadow-xs flex-shrink-0">
                <svg className="w-3.5 h-3.5 text-white fill-white" viewBox="0 0 24 24">
                  <path d="M12 0L14.59 9.41L24 12L14.59 14.59L12 24L9.41 14.59L0 12L9.41 9.41L12 0Z" />
                </svg>
              </div>
              <div className="leading-tight">
                <h3 className="text-xs font-bold text-stone-900 dark:text-white">STL Voice Assistant</h3>
                <div className="flex items-center gap-1.5 text-[10px] text-emerald-600 dark:text-emerald-400 font-medium">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  <span>Online • Multi-Agent Graph</span>
                </div>
              </div>
            </div>

            {/* Header Actions */}
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setVoiceEnabled(!voiceEnabled)}
                title={voiceEnabled ? 'Voice output enabled (click to mute)' : 'Voice output muted (click to unmute)'}
                className="w-7 h-7 rounded-lg text-stone-500 hover:text-stone-800 dark:text-stone-400 dark:hover:text-white hover:bg-stone-200/60 dark:hover:bg-white/10 flex items-center justify-center transition-colors cursor-pointer"
              >
                {voiceEnabled ? (
                  <Volume2 className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />
                ) : (
                  <VolumeX className="w-3.5 h-3.5" />
                )}
              </button>

              <button
                type="button"
                onClick={handleReset}
                title="Restart conversation"
                className="w-7 h-7 rounded-lg text-stone-500 hover:text-stone-800 dark:text-stone-400 dark:hover:text-white hover:bg-stone-200/60 dark:hover:bg-white/10 flex items-center justify-center transition-colors cursor-pointer"
              >
                <RefreshCw className="w-3.5 h-3.5" />
              </button>

              <button
                type="button"
                onClick={() => setIsOpen(false)}
                title="Close chat"
                className="w-7 h-7 rounded-lg text-stone-500 hover:text-stone-800 dark:text-stone-400 dark:hover:text-white hover:bg-stone-200/60 dark:hover:bg-white/10 flex items-center justify-center transition-colors cursor-pointer ml-0.5"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>

          {/* Messages Body */}
          <div className="flex-1 overflow-y-auto p-3.5 space-y-3">
            {messages.map((m) => {
              if (m.sender === 'user') {
                return (
                  <div key={m.id} className="flex justify-end">
                    <div className="max-w-[82%] bg-stone-900 text-white dark:bg-white dark:text-stone-900 rounded-2xl rounded-br-xs px-3.5 py-2 text-xs shadow-xs leading-relaxed">
                      <p className="whitespace-pre-wrap">{m.text}</p>
                      <p className="text-[9px] text-right mt-1 opacity-60">{m.timestamp}</p>
                    </div>
                  </div>
                );
              }

              if (m.sender === 'system') {
                return (
                  <div key={m.id} className="flex justify-center my-1">
                    <span className="text-[10px] bg-rose-50 text-rose-700 dark:bg-rose-950/50 dark:text-rose-300 border border-rose-200 dark:border-rose-900/50 px-2 py-1 rounded-md">
                      {m.text}
                    </span>
                  </div>
                );
              }

              return (
                <div key={m.id} className="flex flex-col items-start gap-1 max-w-[85%]">
                  {m.agentName && (
                    <span className="text-[10px] font-semibold text-indigo-600 dark:text-indigo-400 px-1">
                      {m.agentName}
                    </span>
                  )}

                  <div className="bg-stone-100 dark:bg-stone-800/80 border border-stone-200/70 dark:border-white/5 rounded-2xl rounded-bl-xs px-3.5 py-2.5 text-xs text-stone-800 dark:text-stone-200 space-y-1.5 shadow-2xs leading-relaxed">
                    {/* Tool Badge if called */}
                    {m.toolCalled && (
                      <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-amber-50 dark:bg-amber-950/40 border border-amber-200/70 dark:border-amber-800/40 text-[10px] text-amber-800 dark:text-amber-300 font-mono">
                        <Wrench className="w-3 h-3 flex-shrink-0" />
                        <span className="truncate">Tool: {m.toolCalled}</span>
                      </div>
                    )}

                    <p className="whitespace-pre-wrap">{m.text}</p>

                    <div className="flex items-center justify-between pt-1 text-[9px] text-stone-400 dark:text-stone-500">
                      <span>{m.timestamp}</span>
                      <button
                        type="button"
                        onClick={() => speakText(m.text)}
                        title="Replay voice audio"
                        className="hover:text-stone-700 dark:hover:text-stone-300 flex items-center gap-1 cursor-pointer"
                      >
                        <Volume2 className="w-2.5 h-2.5" /> Speak
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}

            {/* Quick Prompt Chips (when initial conversation) */}
            {messages.length === 1 && (
              <div className="pt-2 space-y-1.5">
                <p className="text-[10px] font-semibold text-stone-400 dark:text-stone-500 px-1 uppercase tracking-wider">
                  Suggested inquiries
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {QUICK_PROMPTS.map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      onClick={() => handleSendMessage(prompt)}
                      className="text-[11px] text-left px-2.5 py-1.5 rounded-lg border border-stone-200 dark:border-white/10 bg-white dark:bg-stone-800/60 hover:bg-stone-50 dark:hover:bg-stone-700/60 text-stone-700 dark:text-stone-300 transition-colors flex items-center justify-between gap-1.5 cursor-pointer shadow-2xs"
                    >
                      <span className="truncate">{prompt}</span>
                      <ChevronRight className="w-3 h-3 text-stone-400 flex-shrink-0" />
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Loading typing indicator */}
            {isLoading && (
              <div className="flex items-center gap-1.5 px-3 py-2 rounded-2xl bg-stone-100 dark:bg-stone-800/80 border border-stone-200/70 dark:border-white/5 w-fit">
                <div className="w-1.5 h-1.5 rounded-full bg-stone-400 dark:bg-stone-500 animate-bounce" style={{ animationDelay: '0ms' }} />
                <div className="w-1.5 h-1.5 rounded-full bg-stone-400 dark:bg-stone-500 animate-bounce" style={{ animationDelay: '150ms' }} />
                <div className="w-1.5 h-1.5 rounded-full bg-stone-400 dark:bg-stone-500 animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Input Bar */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSendMessage();
            }}
            className="p-2.5 border-t border-stone-200/80 dark:border-white/10 bg-stone-50/80 dark:bg-stone-900/80"
          >
            <div className="flex items-center gap-1.5 rounded-xl border border-stone-200 dark:border-white/10 bg-white dark:bg-stone-950 px-2.5 py-1.5 shadow-2xs focus-within:ring-2 focus-within:ring-indigo-500/20 focus-within:border-indigo-500">
              <input
                type="text"
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                placeholder={isListening ? 'Listening...' : 'Type a question or issue...'}
                disabled={isLoading}
                className="flex-1 text-xs bg-transparent border-none outline-none text-stone-900 dark:text-white placeholder:text-stone-400"
              />

              {/* Mic Toggle Button */}
              {recognitionRef.current && (
                <button
                  type="button"
                  onClick={toggleMic}
                  title={isListening ? 'Stop listening' : 'Speak using microphone'}
                  className={`w-7 h-7 rounded-lg flex items-center justify-center transition-colors cursor-pointer ${
                    isListening
                      ? 'bg-rose-500 text-white animate-pulse'
                      : 'text-stone-400 hover:text-stone-700 dark:hover:text-stone-200 hover:bg-stone-100 dark:hover:bg-stone-800'
                  }`}
                >
                  {isListening ? <MicOff className="w-3.5 h-3.5" /> : <Mic className="w-3.5 h-3.5" />}
                </button>
              )}

              {/* Send Button */}
              <button
                type="submit"
                disabled={!inputText.trim() || isLoading}
                title="Send message"
                className="w-7 h-7 rounded-lg bg-stone-900 text-white dark:bg-white dark:text-stone-900 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-stone-800 dark:hover:bg-stone-200 flex items-center justify-center transition-all cursor-pointer flex-shrink-0"
              >
                <Send className="w-3.5 h-3.5" />
              </button>
            </div>
          </form>
        </div>
      )}

      {/* ─── Floating Trigger Pill Button (Matches Screenshot) ────── */}
      <div className="fixed bottom-6 right-6 z-40">
        <button
          type="button"
          onClick={() => setIsOpen((prev) => !prev)}
          className="group flex items-center gap-2.5 px-4 py-2.5 rounded-full bg-white text-stone-900 border border-stone-200/90 hover:border-stone-300 dark:bg-stone-900 dark:text-white dark:border-white/15 dark:hover:border-white/25 shadow-lg hover:shadow-xl hover:scale-105 active:scale-95 transition-all duration-200 cursor-pointer"
          title={isOpen ? 'Minimize Voice Chat' : 'Open Voice Chat'}
        >
          {/* Gradient Circle with 4-Point Star Sparkle */}
          <div className="w-6 h-6 rounded-full bg-gradient-to-tr from-amber-400 via-orange-500 to-indigo-500 flex items-center justify-center shadow-xs flex-shrink-0">
            <svg className="w-3.5 h-3.5 text-white fill-white" viewBox="0 0 24 24">
              <path d="M12 0L14.59 9.41L24 12L14.59 14.59L12 24L9.41 14.59L0 12L9.41 9.41L12 0Z" />
            </svg>
          </div>

          <span className="text-xs font-semibold tracking-wide select-none">
            {isOpen ? 'Close Chat' : 'Voice Chat'}
          </span>
        </button>
      </div>
    </>
  );
}
