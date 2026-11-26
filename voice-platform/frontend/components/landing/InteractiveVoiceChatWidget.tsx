'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Mic, MicOff, X, Send, Sparkles, Volume2, Bot, User, RefreshCw } from 'lucide-react';
import { synthesizeSpeechLive } from '@/lib/audioSamples';

export function InteractiveVoiceChatWidget() {
  const [isOpen, setIsOpen] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [inputText, setInputText] = useState('');
  const [currentNodeId, setCurrentNodeId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Array<{ role: 'agent' | 'user'; text: string }>>([
    { role: 'agent', text: "Hi there! I'm your STL Digital Voice Assistant. How can I assist you with your IT needs today?" }
  ]);
  const [loading, setLoading] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  const [nearBottom, setNearBottom] = useState(false);

  useEffect(() => {
    const open = () => setIsOpen(true);
    window.addEventListener('stl:open-voice-chat', open);
    return () => window.removeEventListener('stl:open-voice-chat', open);
  }, []);

  useEffect(() => {
    const onScroll = () => {
      const scrolledToBottom =
        window.innerHeight + window.scrollY >= document.body.offsetHeight - 240;
      setNearBottom(scrolledToBottom);
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll);
    return () => {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
    };
  }, []);

  const handleSendMessage = async (textToSend?: string) => {
    const text = textToSend || inputText;
    if (!text.trim()) return;

    const userMsg = { role: 'user' as const, text };
    setMessages(prev => [...prev, userMsg]);
    setInputText('');
    setLoading(true);

    try {
      const res = await fetch('/api/simulator/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_message: text,
          message: text,
          current_node_id: currentNodeId,
          history: messages.map(m => ({ role: m.role, content: m.text }))
        })
      });

      if (res.ok) {
        const data = await res.json();
        if (data.next_node_id) {
          setCurrentNodeId(data.next_node_id);
        }
        const reply = data.text || data.response || "I understand your issue and I've processed the next steps for you.";
        setMessages(prev => [...prev, { role: 'agent', text: reply }]);
        
        // Speak response
        const audio = await synthesizeSpeechLive(reply, 'Aarohi');
        if (audio) audio.play();
      } else {
        setMessages(prev => [...prev, { role: 'agent', text: "I'm ready to help with your printer, SAP database, or VPN tickets!" }]);
      }
    } catch (e) {
      setMessages(prev => [...prev, { role: 'agent', text: "I've noted that for you and can help resolve it right away!" }]);
    } finally {
      setLoading(false);
    }
  };

  const toggleRecording = async () => {
    if (isRecording) {
      if (mediaRecorderRef.current) {
        mediaRecorderRef.current.stop();
      }
      setIsRecording(false);
    } else {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const mediaRecorder = new MediaRecorder(stream);
        mediaRecorderRef.current = mediaRecorder;
        audioChunksRef.current = [];

        mediaRecorder.ondataavailable = (e) => {
          if (e.data.size > 0) audioChunksRef.current.push(e.data);
        };

        mediaRecorder.onstop = async () => {
          const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/wav' });
          const formData = new FormData();
          formData.append('file', audioBlob, 'mic.wav');

          try {
            setLoading(true);
            const res = await fetch('/api/stt/transcribe', {
              method: 'POST',
              body: formData
            });
            if (res.ok) {
              const data = await res.json();
              if (data.transcript) {
                handleSendMessage(data.transcript);
              }
            }
          } catch (e) {
            console.error(e);
          } finally {
            setLoading(false);
          }
        };

        mediaRecorder.start();
        setIsRecording(true);
      } catch (err) {
        alert('Please allow microphone access to talk directly to the agent.');
      }
    }
  };

  return (
    <>
      {/* Floating Live Voice Chat Pill Button (Bottom Right) */}
      <div className="fixed bottom-6 right-6 z-50">
        {!isOpen ? (
          <button
            onClick={() => setIsOpen(true)}
            type="button"
            title="Open Live Voice Chat"
            className={`group flex items-center gap-2.5 px-4 py-3 rounded-full bg-stone-900 text-white dark:bg-white dark:text-stone-900 shadow-2xl hover:scale-105 active:scale-95 transition-all cursor-pointer border border-stone-700/30 dark:border-white/20 ${
              nearBottom ? 'opacity-0 translate-y-4 pointer-events-none' : 'opacity-100'
            }`}
          >
            <div className="w-5 h-5 rounded-full bg-indigo-500 animate-spin-slow flex items-center justify-center">
              <Sparkles className="w-3 h-3 text-white fill-current" />
            </div>
            <span className="text-xs font-semibold tracking-wide">Voice Chat</span>
          </button>
        ) : (
          /* Expanded Modal Box */
          <div className="w-[360px] sm:w-[400px] h-[520px] rounded-3xl border border-stone-200 dark:border-white/10 bg-white/95 dark:bg-[#0e1017]/95 shadow-2xl backdrop-blur-2xl flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-200">
            {/* Header */}
            <div className="px-5 py-4 border-b border-stone-200 dark:border-white/10 flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-full bg-indigo-600 flex items-center justify-center text-white shadow-md">
                  <Bot className="w-4 h-4" />
                </div>
                <div>
                  <h4 className="text-xs font-bold text-stone-900 dark:text-white">STL Live Voice Agent</h4>
                  <span className="text-[10px] text-emerald-600 dark:text-emerald-400 flex items-center gap-1 font-medium">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" /> Live
                  </span>
                </div>
              </div>
              <button
                onClick={() => setIsOpen(false)}
                className="w-7 h-7 rounded-full text-stone-400 hover:text-stone-700 dark:hover:text-white flex items-center justify-center"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Chat Body */}
            <div className="flex-1 p-4 overflow-y-auto space-y-3">
              {messages.map((m, idx) => (
                <div
                  key={idx}
                  className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  <div
                    className={`max-w-[80%] px-4 py-2.5 rounded-2xl text-xs leading-relaxed ${
                      m.role === 'user'
                        ? 'bg-stone-900 text-white dark:bg-white dark:text-stone-900'
                        : 'bg-stone-100 dark:bg-stone-800 text-stone-900 dark:text-stone-100 border border-stone-200/50 dark:border-white/5'
                    }`}
                  >
                    {m.text}
                  </div>
                </div>
              ))}
              {loading && (
                <div className="flex justify-start">
                  <div className="px-4 py-2.5 rounded-2xl bg-stone-100 dark:bg-stone-800 text-xs text-stone-500 flex items-center gap-2">
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" /> Thinking...
                  </div>
                </div>
              )}
            </div>

            {/* Mic & Input Footer */}
            <div className="p-3 border-t border-stone-200 dark:border-white/10 bg-stone-50/80 dark:bg-stone-950/80 flex items-center gap-2">
              <button
                onClick={toggleRecording}
                type="button"
                className={`w-10 h-10 rounded-full flex items-center justify-center transition-all cursor-pointer ${
                  isRecording
                    ? 'bg-rose-600 text-white animate-pulse'
                    : 'bg-stone-200 dark:bg-stone-800 text-stone-700 dark:text-stone-200 hover:bg-stone-300'
                }`}
                title={isRecording ? 'Stop Recording' : 'Hold to Speak (Mic)'}
              >
                {isRecording ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
              </button>

              <input
                type="text"
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSendMessage()}
                placeholder="Ask anything or talk..."
                className="flex-1 rounded-full border border-stone-200 dark:border-white/10 bg-white dark:bg-stone-900 px-4 py-2 text-xs text-stone-900 dark:text-white focus:outline-none"
              />

              <button
                onClick={() => handleSendMessage()}
                className="w-8 h-8 rounded-full bg-stone-900 text-white dark:bg-white dark:text-stone-900 flex items-center justify-center hover:scale-105 active:scale-95"
              >
                <Send className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
