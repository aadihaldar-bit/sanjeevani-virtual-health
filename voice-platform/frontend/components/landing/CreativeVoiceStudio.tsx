'use client';

import React, { useState, useRef } from 'react';
import {
  Play,
  Pause,
  Check,
  ChevronLeft,
  ChevronRight,
  Mic,
  MicOff,
  PhoneCall,
  RefreshCw,
  Sliders,
  Sparkles,
  Bot,
  Activity,
  Terminal,
  Copy,
  CheckCheck,
  ShieldCheck,
  ArrowRight,
  Layers,
  Cpu,
  Database,
  Printer,
  FileCode,
  Radio,
  Server,
  Zap,
  Volume2
} from 'lucide-react';
import { synthesizeSpeechLive } from '@/lib/audioSamples';

interface VoiceOption {
  id: string;
  name: string;
  role: string;
  engine: string;
  avatarGradient: string;
  samplePrompt: string;
}

const VOICES_LIST: VoiceOption[] = [
  {
    id: 'aarohi',
    name: 'Aarohi',
    role: 'IT Service Specialist',
    engine: 'OpenAI Neural / Gnani',
    avatarGradient: 'from-amber-400 via-orange-500 to-red-600',
    samplePrompt: 'Hi there! Welcome to STL Digital Service Desk. I can help resolve your printer paper jam, database latency, or reset your network password.'
  },
  {
    id: 'deepak',
    name: 'Deepak',
    role: 'Database & SAP Expert',
    engine: 'OpenAI Neural / Gnani',
    avatarGradient: 'from-blue-600 via-indigo-700 to-slate-950',
    samplePrompt: 'Please verify host port 5432 is open and your pg_hba rules permit client subnet traffic before restarting the instance.'
  },
  {
    id: 'radhika',
    name: 'Radhika',
    role: 'Hardware & SOP Specialist',
    engine: 'OpenAI Neural / Gnani',
    avatarGradient: 'from-purple-400 via-fuchsia-500 to-indigo-700',
    samplePrompt: 'For Brother DCP-L2540DW paper jams: turn off the power, remove the drum unit, clear the jammed sheet, and reset the toner counter.'
  },
  {
    id: 'gaurav',
    name: 'Gaurav',
    role: 'VPN & Network Specialist',
    engine: 'OpenAI Neural / Groq',
    avatarGradient: 'from-teal-400 via-cyan-500 to-blue-700',
    samplePrompt: 'Let us check your GlobalProtect gateway authentication status and renew your corporate Wi-Fi certificate.'
  },
  {
    id: 'alloy',
    name: 'Alloy',
    role: 'Telephony Dispatcher',
    engine: 'OpenAI Neural',
    avatarGradient: 'from-rose-400 via-pink-500 to-purple-700',
    samplePrompt: 'Connected to Tata Smartflo Telephony Bridge. Streaming audio at 8kHz mu-law with bidirectional barge-in detection.'
  },
  {
    id: 'hope',
    name: 'Hope',
    role: 'Customer Success',
    engine: 'OpenAI Neural',
    avatarGradient: 'from-pink-400 via-rose-500 to-purple-600',
    samplePrompt: 'Thank you for reaching out to STL Digital! Your high-priority incident has been escalated with a 15-minute response SLA.'
  },
];

interface AgentScenario {
  id: string;
  title: string;
  iconName: string;
  userPrompt: string;
  intent: string;
  ragDoc: string;
  toolCall: string;
  agentResponse: string;
  voiceSpeaker: string;
}

const AGENT_SCENARIOS: AgentScenario[] = [
  {
    id: 'symphony-ticket',
    title: 'Symphony Summit Incident Lookup',
    iconName: 'database',
    userPrompt: 'Can you check status and assigned engineer for ticket SYM-INC-2026-77252?',
    intent: 'SYMPHONY_INCIDENT_STATUS_QUERY',
    ragDoc: 'Summit ITIL Connector Schema v4.2 - Incident Management Proxy',
    toolCall: 'SUMMIT_RESTWCF.svc/IM_GetIncidentDetails(TicketID="77252", InstanceCode="Faci")',
    agentResponse: 'Incident SYM-INC-2026-77252 is currently Assigned to the Facilities Workgroup. The category is Facility Access and the SLA resolution target is on schedule.',
    voiceSpeaker: 'Gaurav'
  },
  {
    id: 'brother-jam',
    title: 'Brother Printer Paper Jam SOP',
    iconName: 'printer',
    userPrompt: 'My Brother DCP-L2540DW printer is showing Paper Jam Tray 1 on the LCD.',
    intent: 'HARDWARE_SOP_DIAGNOSIS',
    ragDoc: 'Brother DCP-L2540DW Service Manual Section 3: Paper Feed Jam Clearance',
    toolCall: 'execute_sop_retrieval(device="Brother DCP-L2540DW", error="Tray 1 Jam")',
    agentResponse: 'Please open the front cover of the printer, slide out the drum unit assembly, and gently pull out the jammed sheet. Then reinsert the toner cartridge firmly.',
    voiceSpeaker: 'Radhika'
  },
  {
    id: 'sap-db-failover',
    title: 'SAP HANA / Database Latency',
    iconName: 'server',
    userPrompt: 'Our primary PostgreSQL database instance on port 5432 has 120 idle connections and high deadlock alarms.',
    intent: 'INFRA_DATABASE_DEADLOCK_RESOLVE (Confidence: 98.9%)',
    ragDoc: 'STL Cloud DB SOP: Postgres 16 Auto-Kill Blocking Transactions',
    toolCall: 'db_manager.terminate_idle_locks(port=5432, max_idle_sec=120)',
    agentResponse: 'I have identified 3 blocking queries on PID 4821 and terminated idle sessions. Connection latency has dropped to 4 milliseconds.',
    voiceSpeaker: 'Deepak'
  },
  {
    id: 'cisco-vpn',
    title: 'Cisco AnyConnect & BitLocker',
    iconName: 'shield',
    userPrompt: 'I cannot connect to AnyConnect gateway vpn.stltech.in and my BitLocker drive is locked.',
    intent: 'NETWORK_VPN_SECURITY_RESOLVE',
    ragDoc: 'STL Security PKI Guide: Endpoint VPN Certificate Renewal & TPM Keys',
    toolCall: 'security_vault.fetch_bitlocker_recovery(device_id="WIN-STL-8824")',
    agentResponse: 'Your BitLocker recovery key has been securely verified. I am issuing a temporary 24-hour AnyConnect gateway pass to vpn.stltech.in.',
    voiceSpeaker: 'Hemraj'
  }
];

function playSynthesizedChime(type: 'connected' | 'hold' | 'alert' | 'transfer') {
  if (typeof window === 'undefined') return;
  try {
    const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const now = ctx.currentTime;

    if (type === 'connected') {
      [523.25, 659.25, 783.99, 1046.5].forEach((freq, idx) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq, now + idx * 0.1);
        gain.gain.setValueAtTime(0.2, now + idx * 0.1);
        gain.gain.exponentialRampToValueAtTime(0.001, now + idx * 0.1 + 0.35);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(now + idx * 0.1);
        osc.stop(now + idx * 0.1 + 0.4);
      });
    } else if (type === 'alert') {
      [880, 880, 880].forEach((freq, idx) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'triangle';
        osc.frequency.setValueAtTime(freq, now + idx * 0.14);
        gain.gain.setValueAtTime(0.25, now + idx * 0.14);
        gain.gain.exponentialRampToValueAtTime(0.001, now + idx * 0.14 + 0.1);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(now + idx * 0.14);
        osc.stop(now + idx * 0.14 + 0.12);
      });
    } else if (type === 'hold') {
      [440, 554.37, 659.25, 880].forEach((freq) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq, now);
        gain.gain.setValueAtTime(0.15, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 1.4);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(now);
        osc.stop(now + 1.4);
      });
    } else if (type === 'transfer') {
      [697, 1209].forEach((freq) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq, now);
        gain.gain.setValueAtTime(0.2, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.4);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(now);
        osc.stop(now + 0.45);
      });
    }
  } catch (e) {
    console.error('AudioContext chime error', e);
  }
}

interface CreativeVoiceStudioProps {
  activeCategory?: 'studio' | 'agents' | 'telephony';
  onSelectCategory?: (category: 'studio' | 'agents' | 'telephony') => void;
  onOpenConsole?: () => void;
}

export function CreativeVoiceStudio({
  activeCategory = 'studio',
  onSelectCategory,
  onOpenConsole,
}: CreativeVoiceStudioProps) {
  const [activeSubTab, setActiveSubTab] = useState<'tts' | 'stt' | 'cloning' | 'chimes'>('tts');
  const [activeAgentTab, setActiveAgentTab] = useState<'simulator' | 'graph'>('simulator');
  const [activeTelephonyTab, setActiveTelephonyTab] = useState<'dialer' | 'sdk' | 'telemetry'>('dialer');

  // TTS State
  const [selectedVoiceId, setSelectedVoiceId] = useState<string>('aarohi');
  const [selectedLang, setSelectedLang] = useState('English');
  const [selectedTtsEngine, setSelectedTtsEngine] = useState('OpenAI Neural TTS-1');
  const [loading, setLoading] = useState(false);
  const [playing, setPlaying] = useState(false);

  const currentVoice = VOICES_LIST.find(v => v.id === selectedVoiceId) || VOICES_LIST[0];
  const [textInput, setTextInput] = useState<string>(currentVoice.samplePrompt);

  // STT State
  const [isRecording, setIsRecording] = useState(false);
  const [sttEngine, setSttEngine] = useState('Groq Whisper Large-v3-Turbo');
  const [sttTranscript, setSttTranscript] = useState('My Brother printer has a paper feed jam in tray 1.');
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  // Voice Tuning State
  const [speechSpeed, setSpeechSpeed] = useState('1.0');
  const [speechPitch, setSpeechPitch] = useState('1.0');
  const [speechEmotion, setSpeechEmotion] = useState('Helpful & Friendly');

  // Multi-Agent Simulator State
  const [selectedScenarioId, setSelectedScenarioId] = useState<string>('symphony-ticket');
  const [isExecutingAgent, setIsExecutingAgent] = useState(false);
  const [agentStep, setAgentStep] = useState<number>(4);
  const [agentPlaying, setAgentPlaying] = useState(false);
  const activeScenario = AGENT_SCENARIOS.find(s => s.id === selectedScenarioId) || AGENT_SCENARIOS[0];

  // Telephony State
  const [phoneNumber, setPhoneNumber] = useState('+919000000002');
  const [telephonyCarrier, setTelephonyCarrier] = useState('Tata Smartflo (918069879488)');
  const [callStatus, setCallStatus] = useState<string | null>(null);
  const [phoneLoading, setPhoneLoading] = useState(false);

  // SDK Code State
  const [sdkLang, setSdkLang] = useState<'curl' | 'python' | 'ts' | 'ws'>('python');
  const [copied, setCopied] = useState(false);

  const handleSelectVoice = (voice: VoiceOption) => {
    setSelectedVoiceId(voice.id);
    setTextInput(voice.samplePrompt);
    if (playing) {
      if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
        window.speechSynthesis.cancel();
      }
      setPlaying(false);
    }
  };

  const handlePlayTTS = async () => {
    if (playing) {
      if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
        window.speechSynthesis.cancel();
      }
      setPlaying(false);
      return;
    }

    setLoading(true);
    setPlaying(true);
    try {
      const langCode = selectedLang === 'Hindi' ? 'hi-IN' : selectedLang === 'Tamil' ? 'ta-IN' : selectedLang === 'Telugu' ? 'te-IN' : 'en-IN';
      const audio = await synthesizeSpeechLive(textInput, {
        voice: currentVoice.name,
        language: langCode,
        rate: parseFloat(speechSpeed) || 1.0,
        pitch: parseFloat(speechPitch) || 1.0,
        onEnd: () => setPlaying(false),
        onError: () => setPlaying(false),
      });
      if (audio) {
        audio.playbackRate = parseFloat(speechSpeed) || 1.0;
        audio.onended = () => setPlaying(false);
        audio.onerror = () => setPlaying(false);
        audio.play().catch(() => setPlaying(false));
      }
    } finally {
      setLoading(false);
    }
  };

  const handleExecuteScenario = () => {
    setIsExecutingAgent(true);
    setAgentStep(1);
    setTimeout(() => setAgentStep(2), 400);
    setTimeout(() => setAgentStep(3), 900);
    setTimeout(() => {
      setAgentStep(4);
      setIsExecutingAgent(false);
    }, 1500);
  };

  const handlePlayAgentReply = async () => {
    if (agentPlaying) {
      if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
        window.speechSynthesis.cancel();
      }
      setAgentPlaying(false);
      return;
    }

    setAgentPlaying(true);
    const audio = await synthesizeSpeechLive(activeScenario.agentResponse, {
      voice: activeScenario.voiceSpeaker,
      rate: 1.0,
      pitch: 1.0,
      onEnd: () => setAgentPlaying(false),
      onError: () => setAgentPlaying(false),
    });
    if (audio) {
      audio.onended = () => setAgentPlaying(false);
      audio.onerror = () => setAgentPlaying(false);
      audio.play().catch(() => setAgentPlaying(false));
    }
  };

  const toggleRecording = async () => {
    if (isRecording) {
      if (mediaRecorderRef.current) mediaRecorderRef.current.stop();
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
                setSttTranscript(data.transcript);
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
        alert('Please allow microphone access to test Speech to Text.');
      }
    }
  };

  const handleOutboundCall = async () => {
    if (!phoneNumber.trim()) return;
    setPhoneLoading(true);
    setCallStatus(`Connecting outbound trunk via ${telephonyCarrier}...`);
    try {
      const res = await fetch('/api/calls/outbound', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          to_number: phoneNumber.startsWith('+') ? phoneNumber : `+${phoneNumber}`,
          provider: telephonyCarrier.includes('Smartflo') ? 'smartflo' : 'twilio'
        })
      });
      const data = await res.json();
      if (res.ok) {
        setCallStatus(`✓ Live Call Queued! Call SID: ${data.call_sid || data.ref_id || 'CALL-ACTIVE-9921'}. Phone is ringing.`);
      } else {
        setCallStatus(`Error: ${data.detail || 'Carrier endpoint unreachable'}`);
      }
    } catch (e: any) {
      setCallStatus(`Simulation: Outbound carrier request dispatched to ${phoneNumber}`);
    } finally {
      setPhoneLoading(false);
    }
  };

  const codeSnippets: Record<string, string> = {
    curl: `curl -X POST "https://voicebot-agent.stldigitaltech.com/api/calls/outbound" \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer <STL_AUTH_TOKEN>" \\
  -d '{
    "to_number": "+919000000002",
    "provider": "smartflo",
    "agent_id": "brother-sop-specialist",
    "metadata": { "ticket_id": "77252" }
  }'`,
    python: `import httpx
import asyncio

async def dispatch_voice_agent():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://voicebot-agent.stldigitaltech.com/api/workflow/execute",
            headers={"Authorization": "Bearer STL_API_KEY"},
            json={
                "prompt": "Investigate Brother printer paper jam in Tray 1",
                "specialist": "radhika",
                "enable_telephony": True,
                "dest_phone": "+919000000002"
            }
        )
        print("Agent Result:", response.json())

asyncio.run(dispatch_voice_agent())`,
    ts: `import { VoiceAgentClient } from '@stl-digital/voice-sdk';

const client = new VoiceAgentClient({
  apiKey: process.env.STL_VOICE_API_KEY!,
  endpoint: 'https://voicebot-agent.stldigitaltech.com'
});

// Real-time voice agent call dispatch
const session = await client.calls.create({
  phoneNumber: '+919000000002',
  agent: 'symphony-summit-specialist',
  context: { incidentId: 'SYM-INC-2026-77252' },
  onSpeechEvent: (event) => console.log('Caller speech:', event.transcript)
});`,
    ws: `// Full-Duplex Bidirectional Audio Stream (8kHz mu-law / 16kHz PCM)
const ws = new WebSocket("wss://voicebot-agent.stldigitaltech.com/ws/call");

ws.onopen = () => {
  ws.send(JSON.stringify({ event: "start", call_id: "CALL-772" }));
};

ws.onmessage = (event) => {
  const payload = JSON.parse(event.data);
  if (payload.event === "media") {
    playRawPcmAudio(payload.audio_chunk_base64);
  }
};`
  };

  const handleCopyCode = () => {
    navigator.clipboard.writeText(codeSnippets[sdkLang]);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="w-full max-w-5xl mx-auto my-8 px-4">
      <div className="rounded-3xl border border-stone-200/90 dark:border-white/10 bg-[#f7f7f5] dark:bg-[#0c0e15] p-6 sm:p-8 shadow-xl">
        
        {/* ========================================================================= */}
        {/* MODE 1: STL VOICE STUDIO (TTS, STT, Tuning, Chimes)                       */}
        {/* ========================================================================= */}
        {activeCategory === 'studio' && (
          <div className="space-y-6">
            
            {/* 1.1 Text to Speech (TTS) Tab View */}
            {activeSubTab === 'tts' && (
              <div className="rounded-2xl border border-stone-200/80 dark:border-white/10 bg-white dark:bg-[#121520] p-6 sm:p-8 shadow-sm grid grid-cols-1 md:grid-cols-12 gap-8">
                {/* Left Column: Voice Personas */}
                <div className="md:col-span-5 flex flex-col justify-between space-y-3">
                  <div className="space-y-1.5">
                    {VOICES_LIST.map((voice) => {
                      const isSelected = voice.id === selectedVoiceId;
                      return (
                        <div
                          key={voice.id}
                          onClick={() => handleSelectVoice(voice)}
                          className={`flex items-center justify-between p-2.5 rounded-xl cursor-pointer transition-all ${
                            isSelected
                              ? 'bg-stone-100 dark:bg-white/10 shadow-xs'
                              : 'hover:bg-stone-50 dark:hover:bg-white/5'
                          }`}
                        >
                          <div className="flex items-center gap-3 min-w-0">
                            <div className="relative flex-shrink-0">
                              <div className={`w-7 h-7 rounded-full bg-gradient-to-tr ${voice.avatarGradient} shadow-xs`} />
                              {isSelected && (
                                <div className="absolute -top-1 -left-1 w-3.5 h-3.5 rounded-full bg-black text-white dark:bg-white dark:text-black flex items-center justify-center">
                                  <Check className="w-2.5 h-2.5 stroke-[3]" />
                                </div>
                              )}
                            </div>
                            <div className="min-w-0">
                              <p className="text-xs font-semibold text-stone-900 dark:text-white truncate">
                                {voice.name}
                              </p>
                            </div>
                          </div>

                          <div className="flex items-center gap-2">
                            {voice.role && (
                              <span className="text-[11px] text-stone-400 dark:text-stone-500 font-medium truncate max-w-[120px]">
                                {voice.role}
                              </span>
                            )}
                            {isSelected && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handlePlayTTS();
                                }}
                                className="w-7 h-7 rounded-full bg-stone-200 dark:bg-white/10 flex items-center justify-center text-stone-900 dark:text-white hover:scale-105"
                              >
                                {playing ? <Pause className="w-3.5 h-3.5 fill-current" /> : <Play className="w-3.5 h-3.5 fill-current ml-0.5" />}
                              </button>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  <div className="pt-3 border-t border-stone-100 dark:border-white/5 flex items-center justify-between">
                    <button
                      onClick={onOpenConsole}
                      type="button"
                      className="px-3.5 py-1.5 rounded-full border border-stone-200 dark:border-white/10 text-xs font-medium text-stone-700 dark:text-stone-300 hover:bg-stone-50 dark:hover:bg-white/5 transition-all cursor-pointer"
                    >
                      Configure Agents in Console
                    </button>
                    <div className="flex items-center gap-1">
                      <button className="w-6 h-6 rounded-full border border-stone-200 dark:border-white/10 flex items-center justify-center text-stone-500 hover:bg-stone-50">
                        <ChevronLeft className="w-3.5 h-3.5" />
                      </button>
                      <button className="w-6 h-6 rounded-full border border-stone-200 dark:border-white/10 flex items-center justify-center text-stone-500 hover:bg-stone-50">
                        <ChevronRight className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                </div>

                {/* Right Column: Text & Audio Controls */}
                <div className="md:col-span-7 flex flex-col justify-between">
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <p className="text-xs font-medium text-stone-400 dark:text-stone-500">
                        Enter your prompt or dialogue
                      </p>
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] text-stone-400">Engine:</span>
                        <select
                          value={selectedTtsEngine}
                          onChange={(e) => setSelectedTtsEngine(e.target.value)}
                          className="bg-transparent text-xs font-medium text-stone-700 dark:text-stone-300 focus:outline-none cursor-pointer border-b border-stone-200 dark:border-white/10 pb-0.5"
                        >
                          <option value="OpenAI Neural TTS-1">OpenAI Neural TTS-1</option>
                          <option value="Gnani.ai Vach Engine">Gnani.ai Vach (8kHz Telephony)</option>
                          <option value="Groq Llama-3.3 Synthesis">Groq Fast Synthesis</option>
                        </select>
                      </div>
                    </div>

                    <textarea
                      value={textInput}
                      onChange={(e) => setTextInput(e.target.value)}
                      rows={6}
                      className="w-full rounded-2xl border border-transparent bg-stone-50/70 dark:bg-stone-900/60 p-4 text-xs sm:text-sm leading-relaxed text-stone-800 dark:text-stone-200 focus:outline-none focus:bg-stone-100/80 dark:focus:bg-stone-900 transition-all resize-none font-sans"
                      placeholder="Type or paste any text for the neural engine to synthesize..."
                    />
                  </div>

                  <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                      <div className="flex items-center gap-1.5 text-xs font-medium text-stone-700 dark:text-stone-300">
                        <span>🌐</span>
                        <select
                          value={selectedLang}
                          onChange={(e) => setSelectedLang(e.target.value)}
                          className="bg-transparent text-xs font-medium focus:outline-none cursor-pointer"
                        >
                          <option value="English">English (India &amp; Global)</option>
                          <option value="Hindi">Hindi (hi-IN)</option>
                          <option value="Tamil">Tamil (ta-IN)</option>
                          <option value="Telugu">Telugu (te-IN)</option>
                        </select>
                      </div>

                      <div className="flex items-center gap-1 text-xs text-stone-500">
                        <Sliders className="w-3 h-3" />
                        <select
                          value={speechSpeed}
                          onChange={(e) => setSpeechSpeed(e.target.value)}
                          className="bg-transparent text-xs font-medium focus:outline-none cursor-pointer"
                        >
                          <option value="0.8">0.8x Speed</option>
                          <option value="1.0">1.0x Normal</option>
                          <option value="1.2">1.2x Fast</option>
                        </select>
                      </div>
                    </div>

                    <button
                      onClick={handlePlayTTS}
                      disabled={loading}
                      type="button"
                      className="px-7 py-2.5 rounded-full bg-black text-white dark:bg-white dark:text-black text-xs font-bold shadow-md hover:scale-105 active:scale-95 transition-all cursor-pointer flex items-center gap-2"
                    >
                      {loading ? (
                        <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                      ) : playing ? (
                        <>
                          <Pause className="w-3.5 h-3.5 fill-current" />
                          <span>Pause</span>
                        </>
                      ) : (
                        <>
                          <Play className="w-3.5 h-3.5 fill-current" />
                          <span>Play</span>
                        </>
                      )}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* 1.2 Speech to Text (ASR) Tab View */}
            {activeSubTab === 'stt' && (
              <div className="rounded-2xl border border-stone-200/80 dark:border-white/10 bg-white dark:bg-[#121520] p-6 sm:p-8 shadow-sm space-y-6">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-sm font-bold text-stone-900 dark:text-white">Neural Speech Recognition (ASR)</h3>
                    <p className="text-xs text-stone-500 dark:text-stone-400 mt-0.5">
                      Benchmarked for low-latency Indian English, regional accents, and noisy telephony audio.
                    </p>
                  </div>

                  <select
                    value={sttEngine}
                    onChange={(e) => setSttEngine(e.target.value)}
                    className="bg-stone-50 dark:bg-stone-900 border border-stone-200 dark:border-white/10 px-3 py-1.5 rounded-xl text-xs font-medium text-stone-800 dark:text-stone-200 focus:outline-none cursor-pointer"
                  >
                    <option value="Groq Whisper Large-v3-Turbo">Groq Whisper-Large-v3-Turbo</option>
                    <option value="Gnani.ai Speech ASR">Gnani.ai ASR (Indian Accents)</option>
                    <option value="OpenAI Whisper">OpenAI Whisper Cloud</option>
                  </select>
                </div>

                <div className="p-8 rounded-2xl border border-dashed border-stone-300 dark:border-white/20 bg-stone-50 dark:bg-stone-950/50 flex flex-col items-center justify-center text-center">
                  <button
                    onClick={toggleRecording}
                    type="button"
                    className={`w-16 h-16 rounded-full flex items-center justify-center shadow-lg transition-all cursor-pointer ${
                      isRecording
                        ? 'bg-rose-600 text-white animate-pulse'
                        : 'bg-stone-900 text-white dark:bg-white dark:text-stone-900 hover:scale-105'
                    }`}
                  >
                    {isRecording ? <MicOff className="w-6 h-6" /> : <Mic className="w-6 h-6" />}
                  </button>
                  <p className="text-xs font-bold text-stone-800 dark:text-stone-200 mt-4">
                    {isRecording ? 'Listening to speech... Click to finish' : 'Click microphone and speak into your device'}
                  </p>
                  <p className="text-[11px] text-stone-400 mt-1">Direct 16kHz PCM stream to {sttEngine.split(' ')[0]}</p>
                </div>

                <div className="p-4 rounded-xl bg-stone-50 dark:bg-stone-900/60 border border-stone-200 dark:border-white/5">
                  <span className="text-[10px] font-mono text-stone-400 uppercase tracking-wider">Live Transcription</span>
                  <p className="text-xs sm:text-sm font-medium text-stone-900 dark:text-white mt-1">
                    "{sttTranscript}"
                  </p>
                </div>
              </div>
            )}

            {/* 1.3 Voice Tuning & Emotion Tab View */}
            {activeSubTab === 'cloning' && (
              <div className="rounded-2xl border border-stone-200/80 dark:border-white/10 bg-white dark:bg-[#121520] p-6 sm:p-8 shadow-sm space-y-6">
                <div>
                  <h3 className="text-sm font-bold text-stone-900 dark:text-white">Voice Timbre &amp; Emotional Nuance Engine</h3>
                  <p className="text-xs text-stone-500 dark:text-stone-400 mt-0.5">
                    Customize pitch, prosody, and situational empathy for customer escalations.
                  </p>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  <div className="p-4 rounded-xl bg-stone-50 dark:bg-stone-900/60 border border-stone-200 dark:border-white/5 space-y-2">
                    <span className="text-xs font-semibold text-stone-700 dark:text-stone-300">Emotion Style</span>
                    <select
                      value={speechEmotion}
                      onChange={(e) => setSpeechEmotion(e.target.value)}
                      className="w-full bg-white dark:bg-stone-800 border border-stone-200 dark:border-white/10 p-2 rounded-lg text-xs"
                    >
                      <option value="Helpful & Friendly">Helpful &amp; Friendly</option>
                      <option value="Authoritative">Authoritative Technical</option>
                      <option value="Calm & Reassuring">Calm &amp; Reassuring (SLA)</option>
                      <option value="Urgent">Priority Incident Alert</option>
                    </select>
                  </div>

                  <div className="p-4 rounded-xl bg-stone-50 dark:bg-stone-900/60 border border-stone-200 dark:border-white/5 space-y-2">
                    <span className="text-xs font-semibold text-stone-700 dark:text-stone-300">Speech Rate ({speechSpeed}x)</span>
                    <input
                      type="range"
                      min="0.5"
                      max="1.5"
                      step="0.1"
                      value={speechSpeed}
                      onChange={(e) => setSpeechSpeed(e.target.value)}
                      className="w-full cursor-pointer accent-stone-900 dark:accent-white"
                    />
                  </div>

                  <div className="p-4 rounded-xl bg-stone-50 dark:bg-stone-900/60 border border-stone-200 dark:border-white/5 space-y-2">
                    <span className="text-xs font-semibold text-stone-700 dark:text-stone-300">Pitch Tuning ({speechPitch}x)</span>
                    <input
                      type="range"
                      min="0.7"
                      max="1.3"
                      step="0.1"
                      value={speechPitch}
                      onChange={(e) => setSpeechPitch(e.target.value)}
                      className="w-full cursor-pointer accent-stone-900 dark:accent-white"
                    />
                  </div>
                </div>

                <div className="flex justify-end">
                  <button
                    onClick={handlePlayTTS}
                    className="px-6 py-2 rounded-full bg-black text-white dark:bg-white dark:text-black text-xs font-bold shadow-md hover:scale-105 transition-all cursor-pointer"
                  >
                    Synthesize Tuned Persona
                  </button>
                </div>
              </div>
            )}

            {/* 1.4 Audio Chimes & Sound FX Tab View */}
            {activeSubTab === 'chimes' && (
              <div className="rounded-2xl border border-stone-200/80 dark:border-white/10 bg-white dark:bg-[#121520] p-6 sm:p-8 shadow-sm space-y-6">
                <div>
                  <h3 className="text-sm font-bold text-stone-900 dark:text-white">Call Center Audio Chimes &amp; Prompts</h3>
                  <p className="text-xs text-stone-500 dark:text-stone-400 mt-0.5">
                    Pre-rendered telecommunication signaling tones, IVR greetings, and hold streams.
                  </p>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  {[
                    { id: 'connected', title: 'Agent Connected Chime', desc: 'Ascending 4-tone harmonic confirmation', type: 'connected' as const },
                    { id: 'hold', title: 'Soothing Hold Tone', desc: 'Continuous pleasant chord progression', type: 'hold' as const },
                    { id: 'alert', title: 'SLA Escalation Beep', desc: 'Urgent triple-beep priority tone', type: 'alert' as const },
                    { id: 'transfer', title: 'DTMF Transfer Handshake', desc: 'Standard carrier signaling tone', type: 'transfer' as const },
                  ].map((chime) => (
                    <div
                      key={chime.id}
                      className="p-4 rounded-xl bg-stone-50 dark:bg-stone-900/60 border border-stone-200 dark:border-white/5 flex items-center justify-between"
                    >
                      <div>
                        <h4 className="text-xs font-bold text-stone-900 dark:text-white">{chime.title}</h4>
                        <p className="text-[11px] text-stone-500 dark:text-stone-400 mt-0.5">{chime.desc}</p>
                      </div>
                      <button
                        onClick={() => playSynthesizedChime(chime.type)}
                        type="button"
                        className="w-8 h-8 rounded-full bg-stone-900 text-white dark:bg-white dark:text-stone-900 flex items-center justify-center hover:scale-110 active:scale-95 transition-all cursor-pointer shadow-sm"
                      >
                        <Volume2 className="w-4 h-4" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Bottom Sub-Tabs for Voice Studio */}
            <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
              <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-stone-600 dark:text-stone-400">
                {[
                  { id: 'tts', label: 'Text to Speech' },
                  { id: 'stt', label: 'Speech to Text' },
                  { id: 'cloning', label: 'Voice Tuning' },
                  { id: 'chimes', label: 'Audio Chimes & Prompts' },
                ].map((tab) => {
                  const isActive = activeSubTab === tab.id;
                  return (
                    <button
                      key={tab.id}
                      onClick={() => setActiveSubTab(tab.id as any)}
                      className={`px-3.5 py-1.5 rounded-full transition-all cursor-pointer ${
                        isActive
                          ? 'bg-white dark:bg-stone-800 text-stone-900 dark:text-white font-semibold shadow-sm'
                          : 'hover:text-stone-900 dark:hover:text-white'
                      }`}
                    >
                      {tab.label}
                    </button>
                  );
                })}
              </div>

              <button
                onClick={onOpenConsole}
                type="button"
                className="px-5 py-2 rounded-full bg-black text-white dark:bg-white dark:text-black text-xs font-bold shadow-sm hover:scale-105 active:scale-95 transition-all cursor-pointer"
              >
                Launch Console
              </button>
            </div>

          </div>
        )}

        {/* ========================================================================= */}
        {/* MODE 2: STL VOICE AGENTS (Multi-Agent Reasoning & SOP Execution)         */}
        {/* ========================================================================= */}
        {activeCategory === 'agents' && (
          <div className="space-y-6">
            
            {/* Multi-Agent Header */}
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-2 border-b border-stone-200 dark:border-white/10">
              <div>
                <div className="flex items-center gap-2">
                  <Bot className="w-4 h-4 text-indigo-500" />
                  <h3 className="text-sm font-bold text-stone-900 dark:text-white">
                    Autonomous Multi-Agent Execution Graph
                  </h3>
                </div>
                <p className="text-xs text-stone-500 dark:text-stone-400 mt-0.5">
                  Real-time SOP retrieval, Symphony Summit REST WCF integration, and spoken resolution.
                </p>
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={() => setActiveAgentTab('simulator')}
                  className={`px-3 py-1.5 rounded-full text-xs font-semibold cursor-pointer ${
                    activeAgentTab === 'simulator'
                      ? 'bg-indigo-600 text-white shadow-sm'
                      : 'bg-stone-100 dark:bg-stone-800 text-stone-700 dark:text-stone-300'
                  }`}
                >
                  Prompt Simulator
                </button>
                <button
                  onClick={() => setActiveAgentTab('graph')}
                  className={`px-3 py-1.5 rounded-full text-xs font-semibold cursor-pointer ${
                    activeAgentTab === 'graph'
                      ? 'bg-indigo-600 text-white shadow-sm'
                      : 'bg-stone-100 dark:bg-stone-800 text-stone-700 dark:text-stone-300'
                  }`}
                >
                  Visual Node Flow
                </button>
              </div>
            </div>

            {/* 2.1 Multi-Agent Prompt Simulator View */}
            {activeAgentTab === 'simulator' && (
              <div className="grid grid-cols-1 md:grid-cols-12 gap-6">
                
                {/* Left: Enterprise Scenario Picker */}
                <div className="md:col-span-4 space-y-2">
                  <span className="text-[11px] font-mono text-stone-400 uppercase tracking-wider">Select Enterprise Scenario</span>
                  {AGENT_SCENARIOS.map((scenario) => {
                    const isSelected = scenario.id === selectedScenarioId;
                    return (
                      <div
                        key={scenario.id}
                        onClick={() => setSelectedScenarioId(scenario.id)}
                        className={`p-3 rounded-2xl border transition-all cursor-pointer ${
                          isSelected
                            ? 'bg-white dark:bg-stone-900 border-indigo-500/80 shadow-md ring-1 ring-indigo-500/20'
                            : 'bg-stone-50 dark:bg-stone-900/40 border-stone-200 dark:border-white/5 hover:border-stone-300'
                        }`}
                      >
                        <div className="flex items-center gap-2.5">
                          <div className={`w-7 h-7 rounded-lg flex items-center justify-center ${isSelected ? 'bg-indigo-500 text-white' : 'bg-stone-200 dark:bg-stone-800 text-stone-700 dark:text-stone-300'}`}>
                            {scenario.iconName === 'database' ? <Database className="w-4 h-4" /> : scenario.iconName === 'printer' ? <Printer className="w-4 h-4" /> : scenario.iconName === 'server' ? <Server className="w-4 h-4" /> : <ShieldCheck className="w-4 h-4" />}
                          </div>
                          <div className="min-w-0 flex-1">
                            <p className="text-xs font-bold text-stone-900 dark:text-white truncate">{scenario.title}</p>
                            <p className="text-[10px] text-stone-500 truncate">Voice: {scenario.voiceSpeaker}</p>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>

                {/* Right: Multi-Step Agent Execution Pipeline */}
                <div className="md:col-span-8 rounded-2xl border border-stone-200/80 dark:border-white/10 bg-white dark:bg-[#121520] p-6 shadow-sm flex flex-col justify-between space-y-4">
                  
                  {/* User Ingest Input */}
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-[11px] font-mono text-stone-400 uppercase">Incoming Voice Dialogue</span>
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 font-semibold">Active Pipeline</span>
                    </div>
                    <div className="p-3.5 rounded-xl bg-stone-50 dark:bg-stone-900 border border-stone-200 dark:border-white/5 text-xs text-stone-900 dark:text-white font-medium">
                      "{activeScenario.userPrompt}"
                    </div>
                  </div>

                  {/* 4-Step Animated Pipeline Visualizer */}
                  <div className="space-y-2 py-2">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-bold text-stone-800 dark:text-stone-200">Agent Reasoning Steps</span>
                      <button
                        onClick={handleExecuteScenario}
                        disabled={isExecutingAgent}
                        className="text-xs font-bold text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer flex items-center gap-1"
                      >
                        {isExecutingAgent ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3 fill-current" />}
                        Re-run Agent Graph
                      </button>
                    </div>

                    {/* Step 1 */}
                    <div className={`p-2.5 rounded-xl border text-xs flex items-start gap-2.5 transition-all ${agentStep >= 1 ? 'bg-stone-50 dark:bg-stone-900/60 border-indigo-500/40' : 'opacity-40 border-stone-200 dark:border-white/5'}`}>
                      <div className="w-5 h-5 rounded-full bg-indigo-500/20 text-indigo-600 dark:text-indigo-400 flex items-center justify-center text-[10px] font-bold mt-0.5">1</div>
                      <div className="min-w-0 flex-1">
                        <span className="font-semibold text-stone-800 dark:text-stone-200">Intent Routing: </span>
                        <span className="font-mono text-[11px] text-stone-600 dark:text-stone-400">{activeScenario.intent}</span>
                      </div>
                    </div>

                    {/* Step 2 */}
                    <div className={`p-2.5 rounded-xl border text-xs flex items-start gap-2.5 transition-all ${agentStep >= 2 ? 'bg-stone-50 dark:bg-stone-900/60 border-indigo-500/40' : 'opacity-40 border-stone-200 dark:border-white/5'}`}>
                      <div className="w-5 h-5 rounded-full bg-indigo-500/20 text-indigo-600 dark:text-indigo-400 flex items-center justify-center text-[10px] font-bold mt-0.5">2</div>
                      <div className="min-w-0 flex-1">
                        <span className="font-semibold text-stone-800 dark:text-stone-200">Knowledge SOP / RAG: </span>
                        <span className="text-[11px] text-stone-600 dark:text-stone-400">{activeScenario.ragDoc}</span>
                      </div>
                    </div>

                    {/* Step 3 */}
                    <div className={`p-2.5 rounded-xl border text-xs flex items-start gap-2.5 transition-all ${agentStep >= 3 ? 'bg-stone-50 dark:bg-stone-900/60 border-indigo-500/40' : 'opacity-40 border-stone-200 dark:border-white/5'}`}>
                      <div className="w-5 h-5 rounded-full bg-indigo-500/20 text-indigo-600 dark:text-indigo-400 flex items-center justify-center text-[10px] font-bold mt-0.5">3</div>
                      <div className="min-w-0 flex-1">
                        <span className="font-semibold text-stone-800 dark:text-stone-200">Tool Execution: </span>
                        <code className="text-[10px] font-mono text-indigo-600 dark:text-indigo-400">{activeScenario.toolCall}</code>
                      </div>
                    </div>

                    {/* Step 4: Final Spoken Answer */}
                    <div className={`p-3 rounded-xl border transition-all ${agentStep >= 4 ? 'bg-indigo-50/50 dark:bg-indigo-950/20 border-indigo-500/60' : 'opacity-40 border-stone-200 dark:border-white/5'}`}>
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[10px] font-bold uppercase tracking-wider text-indigo-700 dark:text-indigo-300">Spoken Resolution</span>
                        <button
                          onClick={handlePlayAgentReply}
                          className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-indigo-600 text-white text-[10px] font-bold shadow-xs hover:scale-105 transition-all cursor-pointer"
                        >
                          {agentPlaying ? <Pause className="w-3 h-3 fill-current" /> : <Play className="w-3 h-3 fill-current" />}
                          <span>{agentPlaying ? 'Pause Voice' : 'Listen to Spoken Reply'}</span>
                        </button>
                      </div>
                      <p className="text-xs text-stone-800 dark:text-stone-200 leading-relaxed font-medium">
                        "{activeScenario.agentResponse}"
                      </p>
                    </div>
                  </div>

                </div>
              </div>
            )}

            {/* 2.2 Visual Node Flow Diagram */}
            {activeAgentTab === 'graph' && (
              <div className="rounded-2xl border border-stone-200/80 dark:border-white/10 bg-white dark:bg-[#121520] p-8 shadow-sm text-center space-y-6">
                <div className="flex flex-wrap items-center justify-center gap-3">
                  <div className="px-4 py-3 rounded-2xl bg-stone-100 dark:bg-stone-900 border border-stone-200 dark:border-white/10 shadow-sm text-center">
                    <p className="text-[10px] font-mono text-stone-400 uppercase">Input</p>
                    <p className="text-xs font-bold text-stone-900 dark:text-white mt-0.5">Telephony 8kHz RTP</p>
                  </div>
                  <ArrowRight className="w-4 h-4 text-stone-400" />
                  <div className="px-4 py-3 rounded-2xl bg-emerald-50 dark:bg-emerald-950/60 border border-emerald-200 dark:border-emerald-800/40 shadow-sm text-center">
                    <p className="text-[10px] font-mono text-emerald-600 dark:text-emerald-400 uppercase">Router</p>
                    <p className="text-xs font-bold text-emerald-900 dark:text-emerald-200 mt-0.5">Llama-3.3 Intent</p>
                  </div>
                  <ArrowRight className="w-4 h-4 text-stone-400" />
                  <div className="px-4 py-3 rounded-2xl bg-indigo-50 dark:bg-indigo-950/60 border border-indigo-200 dark:border-indigo-800/40 shadow-sm text-center">
                    <p className="text-[10px] font-mono text-indigo-600 dark:text-indigo-400 uppercase">Specialist</p>
                    <p className="text-xs font-bold text-indigo-900 dark:text-indigo-200 mt-0.5">Symphony REST Client</p>
                  </div>
                  <ArrowRight className="w-4 h-4 text-stone-400" />
                  <div className="px-4 py-3 rounded-2xl bg-amber-50 dark:bg-amber-950/60 border border-amber-200 dark:border-amber-800/40 shadow-sm text-center">
                    <p className="text-[10px] font-mono text-amber-600 dark:text-amber-400 uppercase">Synthesis</p>
                    <p className="text-xs font-bold text-amber-900 dark:text-amber-200 mt-0.5">Neural TTS</p>
                  </div>
                </div>

                <div className="max-w-xl mx-auto p-4 rounded-xl bg-stone-50 dark:bg-stone-900/60 border border-stone-200 dark:border-white/5 text-xs text-stone-600 dark:text-stone-400">
                  A streaming full-duplex pipeline with barge-in support, so callers can interrupt the agent naturally mid-response.
                </div>
              </div>
            )}

          </div>
        )}

        {/* ========================================================================= */}
        {/* MODE 3: STL TELEPHONY & API (Carrier Dialing, SDK Code & Telemetry)        */}
        {/* ========================================================================= */}
        {activeCategory === 'telephony' && (
          <div className="space-y-6">
            
            {/* Telephony Header */}
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-2 border-b border-stone-200 dark:border-white/10">
              <div>
                <div className="flex items-center gap-2">
                  <Radio className="w-4 h-4 text-indigo-500" />
                  <h3 className="text-sm font-bold text-stone-900 dark:text-white">
                    Carrier Telephony &amp; Developer Infrastructure
                  </h3>
                </div>
                <p className="text-xs text-stone-500 dark:text-stone-400 mt-0.5">
                  Tata Smartflo SIP trunking, Twilio global voice bridge, and carrier latency telemetry.
                </p>
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={() => setActiveTelephonyTab('dialer')}
                  className={`px-3 py-1.5 rounded-full text-xs font-semibold cursor-pointer ${
                    activeTelephonyTab === 'dialer'
                      ? 'bg-indigo-600 text-white shadow-sm'
                      : 'bg-stone-100 dark:bg-stone-800 text-stone-700 dark:text-stone-300'
                  }`}
                >
                  Live Phone Call
                </button>
                <button
                  onClick={() => setActiveTelephonyTab('sdk')}
                  className={`px-3 py-1.5 rounded-full text-xs font-semibold cursor-pointer ${
                    activeTelephonyTab === 'sdk'
                      ? 'bg-indigo-600 text-white shadow-sm'
                      : 'bg-stone-100 dark:bg-stone-800 text-stone-700 dark:text-stone-300'
                  }`}
                >
                  API SDK Code
                </button>
                <button
                  onClick={() => setActiveTelephonyTab('telemetry')}
                  className={`px-3 py-1.5 rounded-full text-xs font-semibold cursor-pointer ${
                    activeTelephonyTab === 'telemetry'
                      ? 'bg-indigo-600 text-white shadow-sm'
                      : 'bg-stone-100 dark:bg-stone-800 text-stone-700 dark:text-stone-300'
                  }`}
                >
                  Voice Pipeline
                </button>
              </div>
            </div>

            {/* 3.1 Live Outbound Phone Call Tester */}
            {activeTelephonyTab === 'dialer' && (
              <div className="rounded-2xl border border-stone-200/80 dark:border-white/10 bg-white dark:bg-[#121520] p-6 sm:p-8 shadow-sm space-y-6">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-sm font-bold text-stone-900 dark:text-white">Trigger Real Outbound Telephony Call</h3>
                    <p className="text-xs text-stone-500 dark:text-stone-400 mt-0.5">
                      Dispatches a live carrier call to your mobile device connected to our AI Agent graph.
                    </p>
                  </div>

                  <select
                    value={telephonyCarrier}
                    onChange={(e) => setTelephonyCarrier(e.target.value)}
                    className="bg-stone-50 dark:bg-stone-900 border border-stone-200 dark:border-white/10 px-3 py-1.5 rounded-xl text-xs font-medium text-stone-800 dark:text-stone-200 focus:outline-none cursor-pointer"
                  >
                    <option value="Tata Smartflo (918069879488)">Tata Smartflo Pilot (918069879488)</option>
                    <option value="Twilio Trunk (+13527253511)">Twilio Trunk (+1 352-725-3511)</option>
                  </select>
                </div>

                <div className="flex flex-col sm:flex-row gap-3">
                  <input
                    type="tel"
                    value={phoneNumber}
                    onChange={(e) => setPhoneNumber(e.target.value)}
                    placeholder="Enter mobile (e.g. +91 98765 43210)"
                    className="flex-1 rounded-2xl border border-stone-200 dark:border-white/10 bg-stone-50 dark:bg-stone-950 px-4 py-3 text-xs sm:text-sm text-stone-900 dark:text-white focus:outline-none font-mono"
                  />
                  <button
                    onClick={handleOutboundCall}
                    disabled={phoneLoading}
                    className="px-6 py-3 rounded-2xl bg-indigo-600 text-white text-xs font-semibold shadow-md hover:bg-indigo-700 transition-all cursor-pointer flex items-center justify-center gap-2 disabled:opacity-50"
                  >
                    {phoneLoading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <PhoneCall className="w-4 h-4" />}
                    Place Test Call
                  </button>
                </div>

                {callStatus && (
                  <div className="p-4 rounded-xl bg-stone-100 dark:bg-stone-900 border border-stone-200 dark:border-white/10 text-xs font-mono text-stone-800 dark:text-stone-200">
                    {callStatus}
                  </div>
                )}
              </div>
            )}

            {/* 3.2 Developer SDK & API Code Generator */}
            {activeTelephonyTab === 'sdk' && (
              <div className="rounded-2xl border border-stone-200/80 dark:border-white/10 bg-white dark:bg-[#121520] p-6 shadow-sm space-y-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    {[
                      { id: 'python', label: 'Python (Async)' },
                      { id: 'ts', label: 'TypeScript / Node' },
                      { id: 'curl', label: 'cURL REST' },
                      { id: 'ws', label: 'WebSocket Stream' },
                    ].map((tab) => (
                      <button
                        key={tab.id}
                        onClick={() => setSdkLang(tab.id as any)}
                        className={`px-3 py-1 rounded-lg text-xs font-mono font-medium cursor-pointer transition-all ${
                          sdkLang === tab.id
                            ? 'bg-stone-900 text-white dark:bg-white dark:text-black font-semibold'
                            : 'text-stone-500 hover:text-stone-900 dark:hover:text-white'
                        }`}
                      >
                        {tab.label}
                      </button>
                    ))}
                  </div>

                  <button
                    onClick={handleCopyCode}
                    className="flex items-center gap-1 text-xs text-stone-500 hover:text-stone-900 dark:hover:text-white cursor-pointer"
                  >
                    {copied ? <CheckCheck className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
                    <span>{copied ? 'Copied!' : 'Copy'}</span>
                  </button>
                </div>

                <pre className="p-4 rounded-xl bg-[#0d0f17] text-stone-200 text-xs font-mono overflow-x-auto leading-relaxed border border-white/5">
                  <code>{codeSnippets[sdkLang]}</code>
                </pre>
              </div>
            )}

            {/* 3.3 SLA & Latency Telemetry Benchmarks */}
            {activeTelephonyTab === 'telemetry' && (
              <div className="rounded-2xl border border-stone-200/80 dark:border-white/10 bg-white dark:bg-[#121520] p-6 sm:p-8 shadow-sm space-y-6">
                <div>
                  <h3 className="text-sm font-bold text-stone-900 dark:text-white">Real-Time Voice Pipeline</h3>
                  <p className="text-xs text-stone-500 dark:text-stone-400 mt-0.5">
                    Every call flows through four streaming stages, engineered for low-latency, natural conversation.
                  </p>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                  <div className="p-4 rounded-xl bg-stone-50 dark:bg-stone-900 border border-stone-200 dark:border-white/5 text-center">
                    <p className="text-[10px] font-mono text-stone-400 uppercase">Transcription</p>
                    <p className="text-sm font-extrabold text-blue-600 dark:text-blue-400 mt-1">Streaming STT</p>
                    <p className="text-[10px] text-stone-400 mt-0.5">Whisper Large-v3</p>
                  </div>
                  <div className="p-4 rounded-xl bg-stone-50 dark:bg-stone-900 border border-stone-200 dark:border-white/5 text-center">
                    <p className="text-[10px] font-mono text-stone-400 uppercase">Reasoning</p>
                    <p className="text-sm font-extrabold text-emerald-600 dark:text-emerald-400 mt-1">Intent Router</p>
                    <p className="text-[10px] text-stone-400 mt-0.5">Llama-3.3</p>
                  </div>
                  <div className="p-4 rounded-xl bg-stone-50 dark:bg-stone-900 border border-stone-200 dark:border-white/5 text-center">
                    <p className="text-[10px] font-mono text-stone-400 uppercase">Synthesis</p>
                    <p className="text-sm font-extrabold text-amber-600 dark:text-amber-400 mt-1">Neural TTS</p>
                    <p className="text-[10px] text-stone-400 mt-0.5">Vach / OpenAI</p>
                  </div>
                  <div className="p-4 rounded-xl bg-stone-50 dark:bg-stone-900 border border-stone-200 dark:border-white/5 text-center">
                    <p className="text-[10px] font-mono text-stone-400 uppercase">Delivery</p>
                    <p className="text-sm font-extrabold text-purple-600 dark:text-purple-400 mt-1">Full-Duplex</p>
                    <p className="text-[10px] text-stone-400 mt-0.5">Barge-in support</p>
                  </div>
                </div>
              </div>
            )}

          </div>
        )}

      </div>
    </div>
  );
}
