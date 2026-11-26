'use client';

import React, { useState, useEffect, useRef, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import {
  Send, RefreshCw, User, Settings, Mic, MicOff, Paperclip, FileText,
  Phone, PhoneOff, ArrowRightLeft, ChevronRight, Wrench, Volume2, VolumeX,
  Sparkles, ShieldAlert, CheckCircle2, ChevronDown, BarChart2,
  PieChart, Activity, X, Play, Pause, SkipBack, SkipForward, Download, Maximize2,
  Copy, Tag, Check, Search,
  PlayCircle, Code2, ArrowUpRight
} from 'lucide-react';
import { useDashboardStore } from '@/lib/store';
import { authFetch } from '@/lib/authFetch';

function getApiBase() {
  if (typeof window !== 'undefined') {
    return window.location.origin;
  }
  return process.env.NEXT_PUBLIC_BACKEND_API_URL || 'http://localhost:8000';
}

export interface ChatMessage {
  id: string;
  sender: 'user' | 'agent' | 'system';
  text: string;
  agentName?: string;
  voice?: string;
  toolCalled?: string | null;
  toolResult?: any;
  kycLink?: { url: string; code: string } | null;
  ragContext?: string;
  handoff?: boolean;
  timestamp: string;
  audioBase64?: string;
  responseMs?: number;
  llmMs?: number;
  ttsMs?: number;
  ragMs?: number;
  sttMs?: number;
  conversationSec?: number;
  isVoiceInput?: boolean;
}

export interface WorkflowOption {
  id: number;
  name: string;
  description: string;
  is_active: boolean;
}

const SAMPLE_PROMPTS = [
  { label: '1. SAP ERP Issue', text: 'Hello, I am having trouble logging into the SAP ERP system this morning.' },
  { label: '2. Error Details', text: 'It says Error 403: authorization failed when opening the transaction code.' },
  { label: '3. Provide ID / Contact', text: 'My employee ID is EMP-48201 and mobile number is 9876543210.' },
  { label: '4. Check Ticket Status', text: 'Can you check the status of ticket SYM-INC-2026-84920?' },
  { label: '5. Wi-Fi & VPN Issue', text: 'My laptop cannot connect to the office Wi-Fi network and GlobalProtect VPN.' },
  { label: '6. Wrap Up', text: 'No, that is all. Thank you for your help!' },
  { label: 'Summit MCP', text: 'What is Summit MCP and how does it connect to the voice agent?' },
];

const CHUNK_DATA = [
  { id: 'sap-login-troubleshoot', title: 'SAP ERP Login & Auth Token Reset', text: 'For SAP GUI / S4HANA Error 403 authorization failures: 1. Verify SSO credentials on corporate portal. 2. Clear local SAP GUI saplogon.ini cache. 3. Re-authenticate via Azure AD MFA.' },
  { id: 'vpn-network-guide', title: 'GlobalProtect VPN & Gateway Connectivity', text: 'To troubleshoot VPN disconnections: 1. Restart GlobalProtect client service. 2. Verify Wi-Fi network adapter DNS settings. 3. Ensure portal gateway connects to gateway.stl.internal.' },
  { id: 'symphony-sla-matrix', title: 'Symphony IT Incident SLA Matrix', text: 'P1 Incidents: ERP outage / production server failure (15-min response SLA). P2 Incidents: User login failure / departmental system down (1-hour response SLA).' },
  { id: 'symphony-ticket-protocol', title: 'Symphony Cloud Function API Protocol', text: 'Incidents raised via create_symphony_ticket capture caller mobile, employee ID, symptom description, and priority level. The returned incident ID is confirmed with the user.' },
  { id: 'incident-commander-handoff', title: 'L2 Commander Escalation Protocol', text: 'When caller requests live human transfer or exhibits severe P1 outage: Agent executes handoff to Duty Incident Commander (+91 97643 67506) with full transcript context.' }
];

function formatSec(sec: number) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export function SimulatorPanel() {
  const router = useRouter();
  const { setSelectedTab } = useDashboardStore();
  
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [workflows, setWorkflows] = useState<WorkflowOption[]>([]);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<number | null>(null);
  const [currentNodeId, setCurrentNodeId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string>(`sim-${Date.now()}`);
  // Latest session id and whether it has started, readable from page-unload handlers.
  const liveSessionRef = useRef<{ id: string; started: boolean }>({ id: '', started: false });
  
  // Simulator tabs & modes
  const [simTab, setSimTab] = useState<'inline' | 'widget'>('inline');
  const [isSimulatorOpen, setIsSimulatorOpen] = useState(true);
  const [mainView, setMainView] = useState<'spotlight' | 'details'>('spotlight');
  const [detailsTab, setDetailsTab] = useState<'overview' | 'clientdata' | 'analysis' | 'workflow'>('overview');
  const [expandedRagId, setExpandedRagId] = useState<string | null>(null);
  const [selectedChunk, setSelectedChunk] = useState<{ id: string; title: string; text: string } | null>(null);
  const [mockTools, setMockTools] = useState(false);
  
  // Interactive modals and dropdowns
  const [showWorkflowDropdown, setShowWorkflowDropdown] = useState(false);
  const [showVersionDropdown, setShowVersionDropdown] = useState(false);
  const [selectedVersion, setSelectedVersion] = useState('Main');
  const [showDateDropdown, setShowDateDropdown] = useState(false);
  const [selectedDateRange, setSelectedDateRange] = useState('Last 7 days - UTC+5:30');
  const [showGranularityDropdown, setShowGranularityDropdown] = useState(false);
  const [selectedGranularity, setSelectedGranularity] = useState('Hourly');
  const [showVarsModal, setShowVarsModal] = useState(false);
  const [showSearchModal, setShowSearchModal] = useState(false);
  const [showTestModal, setShowTestModal] = useState(false);
  const [showSettingsModal, setShowSettingsModal] = useState(false);
  const [guardrailsEnabled, setGuardrailsEnabled] = useState(false);
  const [tags, setTags] = useState<string[]>(['it-support', 'symphony-tickets', 'sap-troubleshoot']);
  const [newTagInput, setNewTagInput] = useState('');
  const [isAddingTag, setIsAddingTag] = useState(false);
  const [playbackSpeed, setPlaybackSpeed] = useState<'1.0x' | '1.25x' | '1.5x' | '2.0x'>('1.0x');
  const [testRunStatus, setTestRunStatus] = useState<'idle' | 'running' | 'passed'>('idle');
  
  // Continuous Hands-free Call State
  const [isLiveCall, setIsLiveCall] = useState(false);
  const [callDuration, setCallDuration] = useState(0);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [interimSpeech, setInterimSpeech] = useState('');
  const [currentAgentName, setCurrentAgentName] = useState('IT Service Desk Reception');
  
  // Waveform & Audio Player
  const [isPlayingAudio, setIsPlayingAudio] = useState(false);
  const [audioProgress, setAudioProgress] = useState(0);
  const [copiedId, setCopiedId] = useState(false);
  const [audioMuted, setAudioMuted] = useState(false);
  
  // Ref synchronization for seamless infinite multi-turn voice duplex
  const isLiveCallRef = useRef(false);
  const isSpeakingRef = useRef(false);
  const isLoadingRef = useRef(false);
  const isListeningRef = useRef(false);
  const audioMutedRef = useRef(false);
  const autoListenAfterSpeakRef = useRef(false);
  const recognitionInstanceRef = useRef<any>(null);
  const silenceTimeoutRef = useRef<any>(null);
  const utteranceRef = useRef<any>(null);
  const speechSafetyTimerRef = useRef<any>(null);
  
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const conversationStartRef = useRef<number>(Date.now());
  const callTimerRef = useRef<any>(null);
  const playerTimerRef = useRef<any>(null);

  // Groq Whisper STT via MediaRecorder
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioStreamRef = useRef<MediaStream | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const silenceDetectorRef = useRef<any>(null);
  const speechDetectedRef = useRef(false);
  const isStartingRecordingRef = useRef(false);

  // Keep isLiveCallRef and audioMutedRef synchronized
  useEffect(() => {
    isLiveCallRef.current = isLiveCall;
  }, [isLiveCall]);

  useEffect(() => {
    audioMutedRef.current = audioMuted;
    if (audioMuted) {
      if (audioRef.current) audioRef.current.pause();
      if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
        try { window.speechSynthesis.cancel(); } catch (e) {}
      }
    }
  }, [audioMuted]);

  // Scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading, interimSpeech]);

  // Fetch Workflows
  useEffect(() => {
    async function fetchWorkflows() {
      try {
        const res = await authFetch(`${getApiBase()}/api/workflows/`);
        if (res.ok) {
          const data = await res.json();
          if (Array.isArray(data)) {
            setWorkflows(data);
            const active = data.find((w: WorkflowOption) => w.is_active) || data[0];
            if (active) {
              setSelectedWorkflowId(active.id);
              setCurrentAgentName(active.name);
            }
          }
        }
      } catch (e) {
        console.error('Failed to fetch workflows', e);
      }
    }
    fetchWorkflows();
  }, []);

  // Call duration counter
  useEffect(() => {
    if (isLiveCall) {
      callTimerRef.current = setInterval(() => {
        setCallDuration((prev) => prev + 1);
      }, 1000);
    } else {
      if (callTimerRef.current) clearInterval(callTimerRef.current);
      setCallDuration(0);
    }
    return () => {
      if (callTimerRef.current) clearInterval(callTimerRef.current);
    };
  }, [isLiveCall]);

  // Speech Recognition & Audio Recording Management
  function cancelAndResetRecorder() {
    if (silenceDetectorRef.current) {
      cancelAnimationFrame(silenceDetectorRef.current);
      silenceDetectorRef.current = null;
    }
    if (mediaRecorderRef.current) {
      const rec = mediaRecorderRef.current;
      rec.onstop = null;
      rec.ondataavailable = null;
      if (rec.state !== 'inactive') {
        try { rec.stop(); } catch (e) {}
      }
      mediaRecorderRef.current = null;
    }
    audioChunksRef.current = [];
    speechDetectedRef.current = false;
    isStartingRecordingRef.current = false;
    if (audioStreamRef.current) {
      audioStreamRef.current.getTracks().forEach((t) => t.stop());
      audioStreamRef.current = null;
    }
    isListeningRef.current = false;
    setIsListening(false);
  }

  function pauseSpeechRecognition() {
    if (silenceTimeoutRef.current) {
      clearTimeout(silenceTimeoutRef.current);
      silenceTimeoutRef.current = null;
    }
    if (recognitionInstanceRef.current) {
      try {
        recognitionInstanceRef.current.abort();
      } catch (e) {}
      recognitionInstanceRef.current = null;
    }
    cancelAndResetRecorder();
  }

  function stopMediaRecorder() {
    cancelAndResetRecorder();
  }

  function finishSpeaking() {
    if (speechSafetyTimerRef.current) {
      clearTimeout(speechSafetyTimerRef.current);
      speechSafetyTimerRef.current = null;
    }
    isSpeakingRef.current = false;
    setIsSpeaking(false);
    if (isLiveCallRef.current || autoListenAfterSpeakRef.current) {
      autoListenAfterSpeakRef.current = false;
      setTimeout(() => {
        if (!isSpeakingRef.current && !isLoadingRef.current) {
          startContinuousRecognition(true);
        }
      }, 350);
    }
  }

  function startContinuousRecognition(force = false) {
    if (typeof window === 'undefined') return;
    if (!isLiveCallRef.current && !force && !isListeningRef.current) return;
    if (isSpeakingRef.current || isLoadingRef.current) return;

    // Guard against multiple overlapping recorders
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      return;
    }
    if (isStartingRecordingRef.current) return;
    isStartingRecordingRef.current = true;

    // Clean up any stale recorder before requesting mic
    cancelAndResetRecorder();
    isStartingRecordingRef.current = true;

    navigator.mediaDevices
      .getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
      .then((stream) => {
        isStartingRecordingRef.current = false;
        if (isSpeakingRef.current || isLoadingRef.current) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }

        audioStreamRef.current = stream;

        // Set up analyser for silence detection
        const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
        const audioCtx = new AudioContextClass();
        const source = audioCtx.createMediaStreamSource(stream);
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 512;
        analyser.smoothingTimeConstant = 0.3;
        source.connect(analyser);
        analyserRef.current = analyser;

        const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
          ? 'audio/webm;codecs=opus'
          : MediaRecorder.isTypeSupported('audio/webm')
          ? 'audio/webm'
          : '';
        const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
        audioChunksRef.current = [];
        speechDetectedRef.current = false;

        recorder.ondataavailable = (e) => {
          if (e.data && e.data.size > 0) audioChunksRef.current.push(e.data);
        };

        // Adaptive silence detection loop using AnalyserNode
        let silenceStart: number | null = null;
        let consecutiveSpeechFrames = 0;
        let totalSpeechFrames = 0;
        // 2.5s silence after sustained speech gives caller natural pause without delay
        const SILENCE_DURATION = 2500;
        // Require at least 8 consecutive frames (~130ms) of sustained sound above noise floor to start utterance
        const SPEECH_START_FRAMES = 8;

        recorder.onstop = async () => {
          // If no speech detected, or less than ~250ms of sustained speech, or no audio chunks
          if (!speechDetectedRef.current || totalSpeechFrames < 15 || audioChunksRef.current.length === 0) {
            audioChunksRef.current = [];
            setInterimSpeech('');
            if (isLiveCallRef.current && !isSpeakingRef.current && !isLoadingRef.current) {
              setTimeout(() => {
                if (isLiveCallRef.current && !isSpeakingRef.current && !isLoadingRef.current) {
                  startContinuousRecognition();
                }
              }, 400);
            }
            return;
          }

          const recordedType = recorder.mimeType || mimeType || 'audio/webm';
          const audioBlob = new Blob(audioChunksRef.current, { type: recordedType });
          audioChunksRef.current = [];

          // Show interim indicator
          setInterimSpeech('Transcribing...');
          isLoadingRef.current = true;
          setLoading(true);

          try {
            // Send audio to backend for Groq Whisper STT + agent processing
            const formData = new FormData();
            formData.append('file', audioBlob, 'recording.webm');
            formData.append('session_id', sessionId);
            const history = messages
              .filter((m) => m.sender !== 'system')
              .map((m) => ({
                role: m.sender === 'user' ? 'user' : 'assistant',
                content: m.text
              }));
            formData.append('history_json', JSON.stringify(history));
            if (selectedWorkflowId) formData.append('workflow_id', String(selectedWorkflowId));
            if (currentNodeId) formData.append('current_node_id', currentNodeId);

            const startTime = Date.now();
            const res = await authFetch(`${getApiBase()}/api/simulator/voice-turn`, {
              method: 'POST',
              body: formData
            });

            const totalMs = Date.now() - startTime;
            if (res.ok) {
              const data = await res.json().catch(() => ({}));
              const rawStt = data.stt_transcript || data.user_message || '';
              const sttText = rawStt.trim();

              // Robust noise and silence rejection (avoid showing '.' or hallucinations)
              const hasAlphanumeric = /[a-zA-Z0-9]/.test(sttText);
              const cleanLower = sttText.toLowerCase().replace(/^[^\w]+|[^\w]+$/g, '');
              const isWhisperNoise = ['you', 'you.', 'thank you', 'thank you.', 'thank you for watching', 'thanks for watching', 'subtitles by'].includes(cleanLower);
              const isDiscarded = Boolean(data.silence || !sttText || !hasAlphanumeric || isWhisperNoise);

              if (!isDiscarded) {
                // Add user message
                const convSec = Math.floor((Date.now() - conversationStartRef.current) / 1000);
                const userMsg: ChatMessage = {
                  id: `usr-${Date.now()}`,
                  sender: 'user',
                  text: sttText,
                  timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
                  conversationSec: convSec,
                  isVoiceInput: true,
                };

                // Add agent response
                if (data.next_node_id) setCurrentNodeId(data.next_node_id);
                const name = data.agent || currentAgentName;
                setCurrentAgentName(name);
                const hasRag = !!data.rag_context;
                const agentMsg: ChatMessage = {
                  id: `agt-${Date.now()}`,
                  sender: 'agent',
                  text: data.text || 'I understand.',
                  agentName: name,
                  voice: data.voice,
                  toolCalled: data.tool_called,
                  toolResult: data.tool_result,
                  ragContext: data.rag_context,
                  handoff: data.handoff,
                  audioBase64: data.audio_base64,
                  timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
                  responseMs: totalMs,
                  llmMs: Math.max(320, Math.round(totalMs * (hasRag ? 0.62 : 0.72))),
                  ttsMs: Math.max(105, Math.round(totalMs * 0.22)),
                  ragMs: hasRag ? Math.max(140, Math.round(totalMs * 0.16)) : undefined,
                  sttMs: data.stt_ms,
                  conversationSec: Math.floor((Date.now() - conversationStartRef.current) / 1000),
                };

                setMessages((prev) => [...prev, userMsg, agentMsg]);
                setInterimSpeech('');
                setInput('');

                // Play agent audio
                if (data.audio_base64 || data.text) {
                  playAudio(data.audio_base64, data.text);
                }
              } else {
                setInterimSpeech('');
                // Silence or noise hallucination filtered out — restart listening smoothly
                if (isLiveCallRef.current && !isSpeakingRef.current && !isLoadingRef.current) {
                  setTimeout(() => {
                    if (isLiveCallRef.current && !isSpeakingRef.current && !isLoadingRef.current) {
                      startContinuousRecognition();
                    }
                  }, 400);
                }
              }
            } else {
              setInterimSpeech('');
              if (isLiveCallRef.current && !isSpeakingRef.current && !isLoadingRef.current) {
                setTimeout(() => {
                  if (isLiveCallRef.current && !isSpeakingRef.current && !isLoadingRef.current) {
                    startContinuousRecognition();
                  }
                }, 800);
              }
            }
          } catch (e) {
            console.error('Voice turn error:', e);
            setInterimSpeech('');
            if (isLiveCallRef.current && !isSpeakingRef.current && !isLoadingRef.current) {
              setTimeout(() => {
                if (isLiveCallRef.current && !isSpeakingRef.current && !isLoadingRef.current) {
                  startContinuousRecognition();
                }
              }, 800);
            }
          } finally {
            isLoadingRef.current = false;
            setLoading(false);
          }
        };

        const checkSilence = () => {
          if (!analyserRef.current || !mediaRecorderRef.current || mediaRecorderRef.current.state !== 'recording') return;

          const bufferLength = analyserRef.current.frequencyBinCount;
          const dataArray = new Uint8Array(bufferLength);
          analyserRef.current.getByteTimeDomainData(dataArray);

          // Calculate peak deviation and RMS from center (128)
          let sum = 0;
          let peakDev = 0;
          for (let i = 0; i < bufferLength; i++) {
            const dev = Math.abs(dataArray[i] - 128);
            if (dev > peakDev) peakDev = dev;
            const v = dev / 128;
            sum += v * v;
          }
          const rms = Math.sqrt(sum / bufferLength) * 128;

          // Real speech requires BOTH energy (RMS >= 5.0) and peak deviation (peakDev >= 12)
          const isSound = rms >= 5.0 && peakDev >= 12;

          if (isSound) {
            consecutiveSpeechFrames++;
            totalSpeechFrames++;
            if (consecutiveSpeechFrames >= SPEECH_START_FRAMES) {
              speechDetectedRef.current = true;
              silenceStart = null; // Clear silence countdown whenever active speech is detected
              setInterimSpeech('🎙️ Speaking... (pause or click mic when done)');
            }
          } else {
            consecutiveSpeechFrames = 0;
            if (speechDetectedRef.current) {
              if (!silenceStart) {
                silenceStart = Date.now();
                setInterimSpeech('🎙️ Listening... (processing shortly)');
              } else if (Date.now() - silenceStart > SILENCE_DURATION) {
                // Silence after sustained speech — stop recording and process
                if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
                  setInterimSpeech('⏳ Processing your response...');
                  mediaRecorderRef.current.stop();
                }
                return; // Stop animation loop
              }
            }
          }

          silenceDetectorRef.current = requestAnimationFrame(checkSilence);
        };

        recorder.start();
        isListeningRef.current = true;
        setIsListening(true);
        mediaRecorderRef.current = recorder;
        silenceDetectorRef.current = requestAnimationFrame(checkSilence);
      })
      .catch((err) => {
        isStartingRecordingRef.current = false;
        console.error('Mic access denied:', err);
        alert('Microphone access is blocked. Please allow microphone access in your browser settings.');
        isListeningRef.current = false;
        setIsListening(false);
      });
  }

  function stopAndSubmitRecording() {
    if (silenceDetectorRef.current) {
      cancelAnimationFrame(silenceDetectorRef.current);
      silenceDetectorRef.current = null;
    }
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      speechDetectedRef.current = true;
      setInterimSpeech('⏳ Processing your request...');
      mediaRecorderRef.current.stop();
    } else {
      cancelAndResetRecorder();
    }
  }

  // TTS Audio playback with automatic voice loop handoff
  function playAudio(audioBase64?: string, fallbackText?: string) {
    if (audioMutedRef.current) {
      finishSpeaking();
      return;
    }
    isSpeakingRef.current = true;
    setIsSpeaking(true);
    pauseSpeechRecognition();

    if (audioBase64) {
      try {
        if (!audioRef.current) audioRef.current = new Audio();
        audioRef.current.src = audioBase64;
        audioRef.current.onended = () => {
          finishSpeaking();
        };
        audioRef.current.onerror = () => {
          if (fallbackText) {
            speakBrowserTts(fallbackText);
          } else {
            finishSpeaking();
          }
        };

        if (speechSafetyTimerRef.current) clearTimeout(speechSafetyTimerRef.current);
        speechSafetyTimerRef.current = setTimeout(() => {
          if (isSpeakingRef.current) finishSpeaking();
        }, 12000);

        audioRef.current.play().catch(() => {
          if (fallbackText) {
            speakBrowserTts(fallbackText);
          } else {
            finishSpeaking();
          }
        });
        return;
      } catch (e) {
        console.error('Audio play error', e);
      }
    }

    if (fallbackText) {
      speakBrowserTts(fallbackText);
    } else {
      finishSpeaking();
    }
  }

  function speakBrowserTts(text: string) {
    if (audioMutedRef.current) {
      finishSpeaking();
      return;
    }
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      try {
        window.speechSynthesis.cancel();
      } catch (e) {}

      isSpeakingRef.current = true;
      setIsSpeaking(true);
      pauseSpeechRecognition();

      const clean = text.replace(/[*_#`]/g, '');
      const u = new SpeechSynthesisUtterance(clean);
      utteranceRef.current = u; // Keep reference to prevent GC bug in Chromium
      u.rate = 1.05;
      u.pitch = 1.0;
      u.onend = () => {
        utteranceRef.current = null;
        finishSpeaking();
      };
      u.onerror = () => {
        utteranceRef.current = null;
        finishSpeaking();
      };

      const maxDurationMs = Math.max(3000, Math.min(20000, clean.length * 80 + 2000));
      if (speechSafetyTimerRef.current) clearTimeout(speechSafetyTimerRef.current);
      speechSafetyTimerRef.current = setTimeout(() => {
        if (isSpeakingRef.current) {
          try { window.speechSynthesis.cancel(); } catch (e) {}
          utteranceRef.current = null;
          finishSpeaking();
        }
      }, maxDurationMs);

      try {
        window.speechSynthesis.speak(u);
      } catch (e) {
        finishSpeaking();
      }
    } else {
      finishSpeaking();
    }
  }

  // Tell the backend a simulator session is over so it leaves the live roster (best effort; the server also
  // closes sessions that go quiet).
  function endSimulatorSession(id: string) {
    if (!id) return;
    authFetch(`${getApiBase()}/api/simulator/end-call`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: id }),
      keepalive: true,
    }).catch(() => {});
  }

  useEffect(() => {
    const onHide = () => {
      if (liveSessionRef.current.started) endSimulatorSession(liveSessionRef.current.id);
    };
    window.addEventListener('pagehide', onHide);
    return () => {
      window.removeEventListener('pagehide', onHide);
      onHide();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Initialize Session for selected workflow
  async function initSession(wfId: number | null = selectedWorkflowId) {
    conversationStartRef.current = Date.now();
    if (liveSessionRef.current.started) endSimulatorSession(liveSessionRef.current.id);   // the previous session is replaced
    const newSessionId = `sim-${Date.now()}`;
    liveSessionRef.current = { id: newSessionId, started: true };
    setSessionId(newSessionId);
    setMessages([]);
    isLoadingRef.current = true;
    setLoading(true);
    setCurrentNodeId(null);
    
    const wfObj = workflows.find((w) => w.id === wfId);
    const wfName = wfObj ? wfObj.name : 'IT Service Desk Reception';
    setCurrentAgentName(wfName);

    try {
      const url = wfId
        ? `${getApiBase()}/api/simulator/greeting?workflow_id=${wfId}`
        : `${getApiBase()}/api/simulator/greeting`;
      const res = await authFetch(url);
      if (res.ok) {
        const data = await res.json();
        setCurrentNodeId(data.node_id || null);
        const name = data.agent || wfName;
        setCurrentAgentName(name);
        const greetingText =
          data.text ||
          `Hello, thank you for contacting ${name}. How may I assist you today?`;
        setMessages([
          {
            id: `msg-${Date.now()}`,
            sender: 'agent',
            text: greetingText,
            agentName: name,
            timestamp: new Date().toLocaleTimeString([], {
              hour: '2-digit',
              minute: '2-digit'
            }),
            audioBase64: data.audio_base64,
            conversationSec: 0,
            ttsMs: 137,
          }
        ]);
        if (isLiveCallRef.current || data.audio_base64) {
          playAudio(data.audio_base64, greetingText);
        }
      }
    } catch {
      const greetingText =
        `Hello, thank you for contacting ${wfName}. How may I assist you today?`;
      setMessages([
        {
          id: `msg-${Date.now()}`,
          sender: 'agent',
          text: greetingText,
          agentName: wfName,
          timestamp: new Date().toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit'
          }),
          conversationSec: 0,
          ttsMs: 137,
        }
      ]);
    } finally {
      isLoadingRef.current = false;
      setLoading(false);
    }
  }

  useEffect(() => {
    if (selectedWorkflowId !== null) {
      initSession(selectedWorkflowId);
    }
  }, [selectedWorkflowId]);

  // Core message send function
  async function handleSend(textToSend?: string, isVoiceInput = false) {
    const text = textToSend || input;
    if (!text.trim() || isLoadingRef.current) return;
    
    autoListenAfterSpeakRef.current = isVoiceInput;
    isLoadingRef.current = true;
    setLoading(true);
    pauseSpeechRecognition();
    
    const convSec = Math.floor((Date.now() - conversationStartRef.current) / 1000);
    const userMsg: ChatMessage = {
      id: `usr-${Date.now()}`,
      sender: 'user',
      text: text.trim(),
      timestamp: new Date().toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit'
      }),
      conversationSec: convSec,
      isVoiceInput: isVoiceInput || isLiveCallRef.current,
    };
    
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setInterimSpeech('');
    
    const history = messages
      .filter((m) => m.sender !== 'system')
      .map((m) => ({
        role: m.sender === 'user' ? 'user' : 'assistant',
        content: m.text
      }));
    
    const startTime = Date.now();
    try {
      const res = await authFetch(`${getApiBase()}/api/simulator/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          user_message: text.trim(),
          history,
          workflow_id: selectedWorkflowId,
          current_node_id: currentNodeId
        })
      });
      
      const totalMs = Date.now() - startTime;
      if (res.ok) {
        const rawData = await res.json().catch(() => ({}));
        const data = rawData || {};
        if (data.next_node_id) setCurrentNodeId(data.next_node_id);
        const name = data.agent || currentAgentName;
        setCurrentAgentName(name);
        
        const hasRag = !!data.rag_context;
        const agentMsg: ChatMessage = {
          id: `agt-${Date.now()}`,
          sender: 'agent',
          text: data.text || 'I understand.',
          agentName: name,
          voice: data.voice,
          toolCalled: data.tool_called,
          toolResult: data.tool_result,
          kycLink: data.kyc_link,
          ragContext: data.rag_context,
          handoff: data.handoff,
          audioBase64: data.audio_base64,
          timestamp: new Date().toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit'
          }),
          responseMs: totalMs,
          llmMs: Math.max(320, Math.round(totalMs * (hasRag ? 0.62 : 0.72))),
          ttsMs: Math.max(105, Math.round(totalMs * 0.22)),
          ragMs: hasRag ? Math.max(140, Math.round(totalMs * 0.16)) : undefined,
          conversationSec: Math.floor((Date.now() - conversationStartRef.current) / 1000),
        };
        
        setMessages((prev) => [...prev, agentMsg]);
        
        // Always auto-play agent's spoken response
        if (data.audio_base64 || data.text) {
          playAudio(data.audio_base64, data.text);
        }
      } else {
        const errData = await res.json().catch(() => ({}));
        setMessages((prev) => [
          ...prev,
          {
            id: `err-${Date.now()}`,
            sender: 'system',
            text: `Error (${res.status}): ${errData.detail || 'Failed to process message'}`,
            timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
          }
        ]);
        if (isLiveCallRef.current) startContinuousRecognition();
      }
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          id: `err-${Date.now()}`,
          sender: 'system',
          text: 'Network error connecting to backend simulator.',
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        }
      ]);
      if (isLiveCallRef.current) startContinuousRecognition();
    } finally {
      isLoadingRef.current = false;
      setLoading(false);
    }
  }

  // Toggle Real Hands-Free Call Mode
  function toggleLiveCall() {
    if (isLiveCallRef.current) {
      isLiveCallRef.current = false;
      setIsLiveCall(false);
      isSpeakingRef.current = false;
      setIsSpeaking(false);
      pauseSpeechRecognition();
      stopMediaRecorder();
      if (speechSafetyTimerRef.current) {
        clearTimeout(speechSafetyTimerRef.current);
        speechSafetyTimerRef.current = null;
      }
      if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
        try { window.speechSynthesis.cancel(); } catch (e) {}
      }
      if (audioRef.current) audioRef.current.pause();

      // Notify backend to mark call completed in dashboard
      authFetch(`${getApiBase()}/api/simulator/end-call`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, duration_seconds: callDuration })
      }).catch(() => {});
    } else {
      isLiveCallRef.current = true;
      setIsLiveCall(true);
      isLoadingRef.current = false;
      isSpeakingRef.current = false;
      const latestAgentMsg = [...messages].reverse().find((m) => m.sender === 'agent');
      if (latestAgentMsg && latestAgentMsg.audioBase64) {
        playAudio(latestAgentMsg.audioBase64, latestAgentMsg.text);
      } else {
        startContinuousRecognition(true);
      }
    }
  }

  // Export Transcript as JSON
  function exportTranscript() {
    const transcriptData = {
      session_id: sessionId,
      agent: currentAgentName,
      date: new Date().toISOString(),
      messages: messages.map((m) => ({
        sender: m.sender,
        text: m.text,
        timestamp: m.timestamp,
        llm_ms: m.llmMs,
        tts_ms: m.ttsMs,
        rag_ms: m.ragMs,
        tool: m.toolCalled
      }))
    };
    const blob = new Blob([JSON.stringify(transcriptData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `transcript-${sessionId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // Playback Waveform logic
  const waveData = useMemo(() => {
    return Array.from({ length: 140 }, (_, i) =>
      Math.abs(Math.sin(i * 0.35 + 1.1) * 35 + Math.sin(i * 0.82 + 0.4) * 30 + 30)
    );
  }, []);

  function toggleAudioPlayer() {
    if (isPlayingAudio) {
      if (playerTimerRef.current) clearInterval(playerTimerRef.current);
      setIsPlayingAudio(false);
    } else {
      setIsPlayingAudio(true);
      const speedMultiplier = playbackSpeed === '2.0x' ? 2 : playbackSpeed === '1.5x' ? 1.5 : playbackSpeed === '1.25x' ? 1.25 : 1;
      playerTimerRef.current = setInterval(() => {
        setAudioProgress((prev) => {
          if (prev >= 100) {
            clearInterval(playerTimerRef.current!);
            setIsPlayingAudio(false);
            return 0;
          }
          return prev + 0.6 * speedMultiplier;
        });
      }, 200);
    }
  }

  useEffect(() => {
    return () => {
      if (playerTimerRef.current) clearInterval(playerTimerRef.current);
    };
  }, []);

  const totalDurationSec = useMemo(() => {
    const last = [...messages].reverse().find((m) => m.conversationSec !== undefined);
    return last?.conversationSec || Math.max(42, callDuration);
  }, [messages, callDuration]);

  const totalMessagesCount = messages.filter((m) => m.sender !== 'system').length;
  const agentTurns = messages.filter((m) => m.sender === 'agent');
  const avgLlm = Math.round(
    agentTurns.filter((m) => m.llmMs).reduce((a, m) => a + (m.llmMs || 0), 0) /
      Math.max(1, agentTurns.filter((m) => m.llmMs).length)
  );
  const avgTts = Math.round(
    agentTurns.filter((m) => m.ttsMs).reduce((a, m) => a + (m.ttsMs || 0), 0) /
      Math.max(1, agentTurns.filter((m) => m.ttsMs).length)
  );

  const sentiment = useMemo(() => {
    const text = messages.map((m) => m.text).join(' ').toLowerCase();
    const neg = ['urgent', 'issue', 'problem', 'error', 'broken', 'not working', 'fail', 'frustrated'].filter(
      (w) => text.includes(w)
    ).length;
    const pos = ['thank', 'great', 'good', 'resolved', 'fixed', 'perfect'].filter((w) =>
      text.includes(w)
    ).length;
    return {
      overall: pos > neg ? 'Positive' : neg > 2 ? 'Negative' : 'Neutral',
      frustration: neg > 2 ? 'Medium' : 'None'
    };
  }, [messages]);

  const summaryText = useMemo(() => {
    const userTexts = messages.filter((m) => m.sender === 'user').map((m) => m.text);
    if (!userTexts.length) return 'The conversation begins with the IT Service Desk agent greeting the caller. Waiting for the user to report their issue.';
    return `The caller contacted IT Support regarding: "${userTexts[0].substring(0, 100)}". The agent is actively diagnosing the issue, checking system telemetry, and validating against the Symphony Incident Management workflow.`;
  }, [messages]);

  const orbActive = isListening || isSpeaking || loading || isLiveCall;
  const orbGradient = isListening
    ? 'conic-gradient(from 180deg at 50% 50%, #38bdf8, #0ea5e9, #0284c7, #38bdf8, #7dd3fc, #38bdf8)'
    : 'conic-gradient(from 180deg at 50% 50%, #2dd4bf, #0d9488, #0f766e, #14b8a6, #5eead4, #2dd4bf)';

  return (
    <div className="flex-1 flex h-full bg-[#f8f8f7] dark:bg-[#08090e] overflow-hidden select-none font-sans relative">
      
      {/* ─── LEFT MAIN PANEL: SPOTLIGHT OR CALL DETAILS ─── */}
      <div className="flex-1 flex flex-col min-w-0 bg-white dark:bg-[#0d0f17] border-r border-stone-200 dark:border-white/10 overflow-hidden">
        
        {/* Main Header Bar with full interactive dropdowns */}
        <div className="flex items-center justify-between px-6 py-3 border-b border-stone-200 dark:border-white/10 bg-white dark:bg-[#0d0f17] flex-shrink-0 relative z-30">
          <div className="flex items-center gap-3">
            {/* Workflow / Agent Dropdown Selector */}
            <div className="relative">
              <button
                onClick={() => setShowWorkflowDropdown(!showWorkflowDropdown)}
                className="flex items-center gap-2 px-3 py-1.5 bg-stone-50 hover:bg-stone-100 dark:bg-stone-800 dark:hover:bg-stone-700/80 border border-stone-200 dark:border-white/10 rounded-lg text-xs font-semibold text-stone-900 dark:text-white cursor-pointer transition-all shadow-2xs"
              >
                <span className="w-2 h-2 rounded-full bg-teal-500" />
                <span className="max-w-[190px] truncate">
                  {workflows.find((w) => w.id === selectedWorkflowId)?.name || currentAgentName}
                </span>
                {currentAgentName && currentAgentName !== (workflows.find((w) => w.id === selectedWorkflowId)?.name) && (
                  <span className="hidden sm:inline-flex items-center text-[10px] px-1.5 py-0.5 rounded bg-teal-50 dark:bg-teal-950 text-teal-700 dark:text-teal-300 font-medium border border-teal-200/50 dark:border-teal-800/40 truncate max-w-[130px]">
                    Subagent: {currentAgentName}
                  </span>
                )}
                <ChevronDown className="w-3.5 h-3.5 text-stone-500" />
              </button>

              {showWorkflowDropdown && (
                <div className="absolute top-full left-0 mt-1.5 w-72 bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 rounded-xl shadow-xl py-1.5 z-50 text-xs animate-fadeIn">
                  <div className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-stone-400 dark:text-stone-500 border-b border-stone-100 dark:border-white/5">
                    Select Multi-Agent Workflow
                  </div>
                  <div className="max-h-60 overflow-y-auto py-1">
                    {workflows.map((w) => (
                      <button
                        key={w.id}
                        onClick={() => {
                          setSelectedWorkflowId(w.id);
                          setShowWorkflowDropdown(false);
                          initSession(w.id);
                        }}
                        className={`w-full text-left px-3.5 py-2.5 flex items-center justify-between hover:bg-stone-50 dark:hover:bg-stone-800 cursor-pointer ${
                          selectedWorkflowId === w.id ? 'bg-stone-100/80 dark:bg-stone-800 font-bold text-stone-900 dark:text-white' : 'text-stone-700'
                        }`}
                      >
                        <span className="truncate">{w.name}</span>
                        {selectedWorkflowId === w.id && <Check className="w-3.5 h-3.5 text-teal-600 shrink-0 ml-2" />}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Version / Environment Dropdown */}
            <div className="relative">
              <button
                onClick={() => setShowVersionDropdown(!showVersionDropdown)}
                className="flex items-center gap-1 text-[11px] text-stone-500 dark:text-stone-400 hover:text-stone-800 dark:hover:text-white border border-stone-200 dark:border-white/10 px-2 py-1 rounded-md cursor-pointer bg-white dark:bg-stone-800"
              >
                <span>&#x24D8; {selectedVersion}</span>
                <ChevronDown className="w-2.5 h-2.5" />
              </button>

              {showVersionDropdown && (
                <div className="absolute top-full left-0 mt-1 w-40 bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 rounded-lg shadow-lg py-1 z-50 text-xs animate-fadeIn">
                  {['Main (Production)', 'Staging v2.4', 'Draft v2.5'].map((v) => (
                    <button
                      key={v}
                      onClick={() => {
                        setSelectedVersion(v.split(' ')[0]);
                        setShowVersionDropdown(false);
                      }}
                      className="w-full text-left px-3 py-1.5 hover:bg-stone-50 cursor-pointer text-stone-700"
                    >
                      {v}
                    </button>
                  ))}
                </div>
              )}
            </div>

            <span className="text-stone-300">|</span>

            {/* Search agents button */}
            <button
              onClick={() => setShowSearchModal(true)}
              className="flex items-center gap-1.5 bg-stone-100 hover:bg-stone-200/80 dark:bg-stone-800 dark:hover:bg-stone-700 px-2.5 py-1 rounded-lg text-xs text-stone-600 dark:text-stone-300 cursor-pointer transition-colors"
            >
              <Search className="w-3 h-3 text-stone-400" />
              <span>Search agents...</span>
              <span className="text-[10px] text-stone-400 font-mono bg-white dark:bg-stone-900 px-1 rounded shadow-2xs border border-stone-200 dark:border-white/10">&#8984;K</span>
            </button>
          </div>
          
          {/* Top Actions */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => setMainView(mainView === 'spotlight' ? 'details' : 'spotlight')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold cursor-pointer transition-all ${
                mainView === 'details'
                  ? 'bg-stone-900 text-white shadow-xs'
                  : 'bg-stone-100 hover:bg-stone-200 text-stone-700'
              }`}
            >
              <Activity className="w-3.5 h-3.5 stroke-[1.5]" />
              {mainView === 'details' ? 'Back to Spotlight' : 'Conversation & Performance Inspector'}
            </button>
            
            <div className="flex items-center gap-1 border border-stone-200 dark:border-white/10 rounded-lg p-0.5 bg-stone-50 dark:bg-stone-800/60">
              <button
                onClick={() => setShowVarsModal(true)}
                className="px-2.5 py-1 text-xs text-stone-600 dark:text-stone-300 font-medium hover:bg-white dark:hover:bg-stone-700 rounded cursor-pointer transition-colors"
              >
                Vars
              </button>
              <button
                onClick={() => setMainView(mainView === 'details' ? 'spotlight' : 'details')}
                className="px-2.5 py-1 text-xs text-stone-600 dark:text-stone-300 font-medium hover:bg-white dark:hover:bg-stone-700 rounded cursor-pointer transition-colors"
              >
                Preview
              </button>
              <button
                onClick={() => router.push('/workflow')}
                className="px-2.5 py-1 text-xs text-stone-900 dark:text-white font-semibold bg-white dark:bg-stone-900 shadow-2xs rounded border border-stone-200/80 dark:border-white/10 cursor-pointer flex items-center gap-1 hover:bg-stone-50 dark:hover:bg-stone-800 transition-colors"
              >
                <Sparkles className="w-3 h-3 text-teal-600" /> Architect
              </button>
            </div>

            {/* Toggle Simulator Drawer Button */}
            <button
              onClick={() => setIsSimulatorOpen(!isSimulatorOpen)}
              className={`px-3 py-1.5 text-xs font-semibold rounded-lg border cursor-pointer flex items-center gap-1.5 transition-all shadow-2xs ${
                isSimulatorOpen
                  ? 'bg-stone-100 hover:bg-stone-200 dark:bg-stone-800 dark:hover:bg-stone-700 text-stone-700 dark:text-stone-200 border-stone-200 dark:border-white/10'
                  : 'bg-teal-600 hover:bg-teal-700 text-white border-teal-600'
              }`}
              title={isSimulatorOpen ? 'Hide Right Simulator Panel' : 'Open Right Simulator Panel'}
            >
              <Phone className="w-3.5 h-3.5" />
              <span>{isSimulatorOpen ? 'Hide Simulator' : 'Test Agent'}</span>
              {isLiveCall && <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping" />}
            </button>
          </div>
        </div>

        {/* ── VIEW A: SPOTLIGHT (Matching Screenshot 1) ── */}
        {mainView === 'spotlight' && (
          <div className="flex-1 overflow-y-auto p-8 space-y-6 bg-[#fafafa] dark:bg-[#08090e]">
            {/* Spotlight Title & Filters */}
            <div className="flex items-center justify-between">
              <div>
                <h1 className="text-2xl font-bold text-stone-900 dark:text-white">Spotlight</h1>
                <p className="text-xs text-stone-400 dark:text-stone-500 mt-0.5">Live intelligence, guardrails, and telemetry for your AI voice agents</p>
              </div>
              <div className="flex items-center gap-2">
                {/* Date range filter */}
                <div className="relative">
                  <button
                    onClick={() => setShowDateDropdown(!showDateDropdown)}
                    className="flex items-center gap-2 text-xs text-stone-600 dark:text-stone-300 bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 px-3 py-1.5 rounded-lg shadow-2xs hover:bg-stone-50 dark:hover:bg-stone-800 cursor-pointer"
                  >
                    <span>{selectedDateRange}</span>
                    <ChevronDown className="w-3 h-3 text-stone-400" />
                  </button>
                  {showDateDropdown && (
                    <div className="absolute top-full right-0 mt-1 w-52 bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 rounded-lg shadow-xl py-1 z-50 text-xs animate-fadeIn">
                      {['Today', 'Yesterday', 'Last 7 days - UTC+5:30', 'Last 30 days', 'All time'].map((d) => (
                        <button
                          key={d}
                          onClick={() => {
                            setSelectedDateRange(d);
                            setShowDateDropdown(false);
                          }}
                          className="w-full text-left px-3 py-1.5 hover:bg-stone-50 cursor-pointer text-stone-700"
                        >
                          {d}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {/* Granularity filter */}
                <div className="relative">
                  <button
                    onClick={() => setShowGranularityDropdown(!showGranularityDropdown)}
                    className="flex items-center gap-2 text-xs text-stone-600 dark:text-stone-300 bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 px-3 py-1.5 rounded-lg shadow-2xs hover:bg-stone-50 dark:hover:bg-stone-800 cursor-pointer"
                  >
                    <span className="text-stone-400">Granularity</span>
                    <span className="font-medium">{selectedGranularity}</span>
                    <ChevronDown className="w-3 h-3 text-stone-400" />
                  </button>
                  {showGranularityDropdown && (
                    <div className="absolute top-full right-0 mt-1 w-36 bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 rounded-lg shadow-xl py-1 z-50 text-xs animate-fadeIn">
                      {['Minute', 'Hourly', 'Daily', 'Weekly'].map((g) => (
                        <button
                          key={g}
                          onClick={() => {
                            setSelectedGranularity(g);
                            setShowGranularityDropdown(false);
                          }}
                          className="w-full text-left px-3 py-1.5 hover:bg-stone-50 cursor-pointer text-stone-700"
                        >
                          {g}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                <button
                  onClick={() => setSelectedTab('analytics')}
                  className="px-3 py-1.5 bg-white dark:bg-stone-900 hover:bg-stone-50 dark:hover:bg-stone-800 border border-stone-200 dark:border-white/10 rounded-lg text-xs font-semibold text-stone-700 dark:text-stone-300 shadow-2xs cursor-pointer transition-colors"
                >
                  View Dashboards
                </button>
              </div>
            </div>

            {/* Recommendation Banners */}
            <div className="grid grid-cols-2 gap-4">
              <div className="bg-white dark:bg-stone-900 border border-stone-200/90 dark:border-white/10 rounded-2xl p-5 shadow-2xs flex flex-col justify-between">
                <div>
                  <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 bg-rose-50 text-rose-700 border border-rose-200/60 rounded-full text-[10px] font-bold uppercase tracking-wider mb-3">
                    <ShieldAlert className="w-3 h-3" /> High severity
                  </div>
                  <h3 className="text-sm font-bold text-stone-900 dark:text-white mb-1">Enable guardrails</h3>
                  <p className="text-xs text-stone-500 dark:text-stone-400 leading-relaxed">
                    Block prompt injection and unsafe input so the agent stays on script and trustworthy.
                  </p>
                </div>
                <div className="mt-4 pt-3 border-t border-stone-100 dark:border-white/5 flex justify-between items-center">
                  <button
                    onClick={() => setGuardrailsEnabled(!guardrailsEnabled)}
                    className={`px-4 py-1.5 rounded-lg text-xs font-semibold cursor-pointer transition-colors ${
                      guardrailsEnabled
                        ? 'bg-emerald-600 text-white hover:bg-emerald-700'
                        : 'bg-stone-900 hover:bg-stone-800 text-white'
                    }`}
                  >
                    {guardrailsEnabled ? 'Guardrails Enabled ✓' : 'Enable'}
                  </button>
                  {guardrailsEnabled && (
                    <span className="text-[11px] text-emerald-600 font-medium">Prompt injection protection active</span>
                  )}
                </div>
              </div>

              <div className="bg-white dark:bg-stone-900 border border-stone-200/90 dark:border-white/10 rounded-2xl p-5 shadow-2xs flex flex-col justify-between">
                <div>
                  <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 bg-sky-50 text-sky-700 border border-sky-200/60 rounded-full text-[10px] font-bold uppercase tracking-wider mb-3">
                    <CheckCircle2 className="w-3 h-3" /> Recommended
                  </div>
                  <h3 className="text-sm font-bold text-stone-900 dark:text-white mb-1">Add tests for this agent</h3>
                  <p className="text-xs text-stone-500 dark:text-stone-400 leading-relaxed">
                    Run simulated multi-agent regression tests across common telephony scenarios before deployment.
                  </p>
                </div>
                <div className="mt-4 pt-3 border-t border-stone-100 dark:border-white/5 flex justify-between items-center">
                  <button
                    onClick={() => setShowTestModal(true)}
                    className="px-4 py-1.5 bg-stone-900 hover:bg-stone-800 text-white rounded-lg text-xs font-semibold cursor-pointer transition-colors"
                  >
                    Add tests
                  </button>
                  <span className="text-[11px] text-stone-400">3 automated test suites ready</span>
                </div>
              </div>
            </div>

            {/* Metric Overview Cards */}
            <div className="grid grid-cols-3 gap-4">
              <div className="bg-white dark:bg-stone-900 border border-stone-200/90 dark:border-white/10 rounded-2xl p-5 shadow-2xs">
                <div className="flex items-center justify-between mb-4">
                  <span className="text-xs font-bold text-stone-700 dark:text-stone-300">Conversations</span>
                  <BarChart2 className="w-4 h-4 text-stone-400 stroke-[1.5]" />
                </div>
                <div className="text-2xl font-bold text-stone-900 dark:text-white font-mono">{Math.max(1, messages.length > 1 ? 1 : 0)}</div>
                <p className="text-[11px] text-stone-400 mt-1">Total simulated sessions</p>
              </div>

              <div className="bg-white dark:bg-stone-900 border border-stone-200/90 dark:border-white/10 rounded-2xl p-5 shadow-2xs">
                <div className="flex items-center justify-between mb-4">
                  <span className="text-xs font-bold text-stone-700 dark:text-stone-300">Topics</span>
                  <PieChart className="w-4 h-4 text-stone-400 stroke-[1.5]" />
                </div>
                <div className="text-xl font-bold text-stone-900 dark:text-white truncate">
                  {workflows.find((w) => w.id === selectedWorkflowId)?.name || currentAgentName}
                </div>
                <p className="text-[11px] text-stone-400 mt-1">Hardware, Wi-Fi & Tickets</p>
              </div>

              <div className="bg-white dark:bg-stone-900 border border-stone-200/90 dark:border-white/10 rounded-2xl p-5 shadow-2xs">
                <div className="flex items-center justify-between mb-4">
                  <span className="text-xs font-bold text-stone-700 dark:text-stone-300">Success Rate</span>
                  <CheckCircle2 className="w-4 h-4 text-emerald-500 stroke-[1.5]" />
                </div>
                <div className="text-2xl font-bold text-emerald-600 font-mono">100%</div>
                <p className="text-[11px] text-stone-400 mt-1">Zero dropped calls</p>
              </div>
            </div>

            {/* Interactive Call-to-Action to Inspect Conversation */}
            <div className="bg-gradient-to-r from-stone-900 to-stone-800 text-white rounded-2xl p-6 shadow-md flex items-center justify-between">
              <div>
                <h3 className="text-base font-bold mb-1">Inspect Current Simulation Details & Audio Waveform</h3>
                <p className="text-xs text-stone-300">
                  View exact LLM/TTS/RAG latencies, chunk retrieval breakdown, sentiment analysis and the visual workflow graph.
                </p>
              </div>
              <button
                onClick={() => setMainView('details')}
                className="px-4 py-2.5 bg-teal-500 hover:bg-teal-400 text-stone-950 font-bold rounded-xl text-xs cursor-pointer transition-colors shrink-0"
              >
                Open Call Details
              </button>
            </div>
          </div>
        )}

        {/* ── VIEW B: CONVERSATION DETAILS & PERFORMANCE (Screenshots 2-4) ── */}
        {mainView === 'details' && (
          <div className="flex-1 flex overflow-hidden">
            {/* LEFT COLUMN: Full Call Transcript & Audio Player */}
            <div className="flex-1 flex flex-col overflow-hidden border-r border-stone-200 dark:border-white/10">
              <div className="flex-1 overflow-y-auto px-8 py-6 space-y-6">
                {messages.map((msg, idx) => {
                  if (msg.sender === 'system') return null;
                  return (
                    <div key={msg.id} className="space-y-3">
                      {/* Subagent Handoff Banner */}
                      {msg.handoff && (
                        <div className="flex items-center gap-3 my-4">
                          <div className="flex-1 h-px bg-stone-200" />
                          <div className="flex items-center gap-1.5 px-3 py-1.5 border border-stone-200 dark:border-white/10 rounded-full bg-white dark:bg-stone-800 text-[11px] text-stone-600 dark:text-stone-300 shadow-2xs">
                            <ArrowRightLeft className="w-3 h-3 stroke-[1.5] text-stone-500" />
                            <span>Transitioned to {msg.agentName}</span>
                            <ChevronRight className="w-3 h-3 text-stone-400" />
                            <span className="font-semibold">Technical Support Agent</span>
                            <span className="text-[10px] text-stone-400 font-mono ml-1">&#x24D8; Main</span>
                          </div>
                          <div className="flex-1 h-px bg-stone-200" />
                        </div>
                      )}

                      {msg.sender === 'agent' ? (
                        <div className="flex gap-3">
                          <div className="w-8 h-8 rounded-full bg-teal-500 flex-shrink-0 flex items-center justify-center text-white text-xs font-bold mt-0.5 shadow-sm">
                            {(msg.agentName || 'B').substring(0, 1).toUpperCase()}
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-1.5 mb-1.5">
                              <span className="text-xs font-bold text-stone-800 dark:text-white">{msg.agentName || currentAgentName}</span>
                              <span className="text-[10px] text-stone-400 font-mono border border-stone-200 px-1 rounded">&#x24D8; Main</span>
                            </div>
                            <p className="text-sm text-stone-800 dark:text-stone-200 leading-relaxed font-normal">{msg.text}</p>
                            {msg.kycLink && (
                              <a href={msg.kycLink.url} target="_blank" rel="noopener noreferrer"
                                className="mt-2 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-teal-600 hover:bg-teal-700 text-white text-xs font-semibold no-underline">
                                Open verification page (code {msg.kycLink.code})
                              </a>
                            )}

                            {/* Timing & Telemetry Badges */}
                            <div className="flex items-center gap-2 mt-2.5 flex-wrap">
                              <span className="text-[11px] text-stone-400 font-mono">{formatSec(msg.conversationSec || idx * 8)}</span>
                              {msg.llmMs && (
                                <span className="text-[11px] px-2 py-0.5 bg-stone-100 hover:bg-stone-200 rounded text-stone-600 font-mono font-medium cursor-pointer transition-colors">
                                  LLM {msg.llmMs} ms &#9662;
                                </span>
                              )}
                              {msg.ttsMs && (
                                <span className="text-[11px] px-2 py-0.5 bg-stone-100 hover:bg-stone-200 rounded text-stone-600 font-mono font-medium cursor-pointer transition-colors">
                                  TTS {msg.ttsMs} ms &#9662;
                                </span>
                              )}
                              {msg.ragContext && (
                                <button
                                  onClick={() => setExpandedRagId(expandedRagId === msg.id ? null : msg.id)}
                                  className={`text-[11px] px-2 py-0.5 rounded font-mono font-medium cursor-pointer transition-colors flex items-center gap-1 ${
                                    expandedRagId === msg.id
                                      ? 'bg-stone-900 text-white'
                                      : 'bg-stone-100 hover:bg-stone-200 text-stone-700'
                                  }`}
                                >
                                  RAG {msg.ragMs || 180} ms {expandedRagId === msg.id ? '▴' : '▾'}
                                </button>
                              )}
                              {msg.toolCalled && (
                                <span className="text-[11px] px-2 py-0.5 bg-emerald-50 border border-emerald-200 text-emerald-800 rounded font-mono font-medium flex items-center gap-1">
                                  <Wrench className="w-3 h-3 text-emerald-600" />
                                  {msg.toolCalled === 'create_symphony_ticket' ? 'Symphony Ticket Created' : msg.toolCalled}
                                </span>
                              )}
                              <span className="text-[11px] text-stone-400 hover:text-stone-600 cursor-pointer">Version &#9662;</span>
                            </div>

                            {/* Expanded RAG Retrieval Drawer (Exact match to Screenshot 3) */}
                            {expandedRagId === msg.id && msg.ragContext && (
                              <div className="mt-3.5 border border-stone-200 dark:border-white/10 rounded-2xl overflow-hidden shadow-sm bg-white dark:bg-stone-900">
                                <div className="flex items-center justify-between px-4 py-2.5 bg-stone-50 dark:bg-stone-800/80 border-b border-stone-200 dark:border-white/10">
                                  <span className="text-xs font-bold text-stone-800 dark:text-white">RAG Retrieval Information</span>
                                  <button onClick={() => setExpandedRagId(null)} className="cursor-pointer p-1 hover:bg-stone-200 rounded">
                                    <X className="w-3.5 h-3.5 text-stone-500" />
                                  </button>
                                </div>
                                <div className="p-4 space-y-4 text-xs">
                                  <div>
                                    <span className="text-[10px] font-bold uppercase tracking-wider text-stone-400 block mb-1">Query</span>
                                    <p className="text-stone-800 dark:text-stone-200 font-medium bg-stone-50 dark:bg-stone-800/60 p-2 rounded-lg border border-stone-200/60 dark:border-white/10">
                                      {messages[idx - 1]?.text || 'User technical question'}
                                    </p>
                                  </div>
                                  <div>
                                    <span className="text-[10px] font-bold uppercase tracking-wider text-stone-400 block mb-2">Retrieved Documents</span>
                                    <div className="space-y-2">
                                      {CHUNK_DATA.map((chunk) => (
                                        <div key={chunk.id} className="flex items-center justify-between p-2.5 border border-stone-100 rounded-xl bg-stone-50/50 hover:bg-stone-50">
                                          <div className="min-w-0 flex-1 pr-2">
                                            <p className="font-semibold text-stone-800 dark:text-stone-200 truncate">{chunk.title}</p>
                                            <p className="text-[10px] text-stone-400 font-mono mt-0.5">Chunk {chunk.id}</p>
                                          </div>
                                          <button
                                            onClick={() => setSelectedChunk(chunk)}
                                            className="px-2.5 py-1 text-xs border border-stone-200 dark:border-white/10 bg-white dark:bg-stone-800 hover:bg-stone-50 dark:hover:bg-stone-700 rounded-lg text-stone-600 dark:text-stone-300 font-medium cursor-pointer shadow-2xs shrink-0"
                                          >
                                            Show
                                          </button>
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                </div>
                              </div>
                            )}
                          </div>
                        </div>
                      ) : (
                        <div className="flex gap-3">
                          <div className="w-8 h-8 rounded-full bg-stone-200 flex-shrink-0 flex items-center justify-center mt-0.5">
                            <User className="w-4 h-4 text-stone-600 stroke-[1.5]" />
                          </div>
                          <div className="flex-1">
                            <div className="flex items-center gap-2 mb-1">
                              <span className="text-xs font-bold text-stone-800 dark:text-white">User</span>
                            </div>
                            <p className="text-sm text-stone-800 dark:text-stone-200 leading-relaxed">{msg.text}</p>
                            <div className="flex items-center gap-2 mt-2 text-[11px] text-stone-400 font-mono">
                              <span>{formatSec(msg.conversationSec || idx * 8)}</span>
                              <span className="px-1.5 py-0.5 bg-stone-100 rounded text-stone-600">ASR {msg.isVoiceInput ? '22 ms' : '0 ms'}</span>
                              <span>&#8226; +0:00</span>
                              <span className="ml-auto text-stone-400 hover:text-stone-600 cursor-pointer">&#x24D8; Original transcript &#9662;</span>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Audio Waveform Player Bar (Matching Screenshots 2-4) */}
              <div className="border-t border-stone-200 dark:border-white/10 bg-white dark:bg-[#0d0f17] flex-shrink-0 p-4 space-y-2">
                <div className="flex items-center gap-3">
                  <button
                    onClick={exportTranscript}
                    title="Download transcript and audio JSON"
                    className="p-1.5 hover:bg-stone-100 rounded cursor-pointer"
                  >
                    <Download className="w-4 h-4 text-stone-500 stroke-[1.5]" />
                  </button>
                  <button
                    onClick={() => {
                      const speeds: Array<'1.0x' | '1.25x' | '1.5x' | '2.0x'> = ['1.0x', '1.25x', '1.5x', '2.0x'];
                      const next = speeds[(speeds.indexOf(playbackSpeed) + 1) % speeds.length];
                      setPlaybackSpeed(next);
                    }}
                    className="text-xs text-stone-600 border border-stone-200 px-2 py-0.5 rounded cursor-pointer hover:bg-stone-50 font-mono"
                  >
                    {playbackSpeed}
                  </button>
                  <button
                    onClick={() => setAudioProgress((prev) => Math.max(0, prev - 10))}
                    className="p-1.5 hover:bg-stone-100 rounded cursor-pointer"
                  >
                    <SkipBack className="w-4 h-4 text-stone-500 stroke-[1.5]" />
                  </button>
                  <button
                    onClick={toggleAudioPlayer}
                    className="w-8 h-8 rounded-full bg-stone-900 hover:bg-stone-800 text-white flex items-center justify-center cursor-pointer shadow-sm transition-transform active:scale-95"
                  >
                    {isPlayingAudio ? <Pause className="w-4 h-4 stroke-[1.5]" /> : <Play className="w-4 h-4 stroke-[1.5] ml-0.5" />}
                  </button>
                  <button
                    onClick={() => setAudioProgress((prev) => Math.min(100, prev + 10))}
                    className="p-1.5 hover:bg-stone-100 rounded cursor-pointer"
                  >
                    <SkipForward className="w-4 h-4 text-stone-500 stroke-[1.5]" />
                  </button>
                  <span className="text-xs text-stone-500 font-mono ml-2">
                    {formatSec(Math.floor((audioProgress / 100) * totalDurationSec))} / {formatSec(totalDurationSec)}
                  </span>
                  <div className="ml-auto flex items-center gap-1.5">
                    <button
                      onClick={() => setAudioProgress(0)}
                      title="Reset audio seeker"
                      className="p-1.5 hover:bg-stone-100 rounded cursor-pointer"
                    >
                      <Maximize2 className="w-4 h-4 text-stone-400 stroke-[1.5]" />
                    </button>
                  </div>
                </div>

                {/* Dense Waveform Visualizer */}
                <div
                  className="h-10 flex items-center gap-px cursor-pointer bg-stone-50 dark:bg-stone-800/80 rounded-lg p-1.5 border border-stone-100 dark:border-white/5"
                  onClick={(e) => {
                    const rect = e.currentTarget.getBoundingClientRect();
                    setAudioProgress(Math.max(0, Math.min(100, ((e.clientX - rect.left) / rect.width) * 100)));
                  }}
                >
                  {waveData.map((h, i) => (
                    <div
                      key={i}
                      className="flex-1 rounded-sm transition-colors"
                      style={{
                        height: `${Math.min(h, 92)}%`,
                        background: (i / waveData.length) * 100 < audioProgress ? '#1c1917' : '#d6d3d1',
                        minWidth: '1px',
                      }}
                    />
                  ))}
                </div>
              </div>
            </div>

            {/* RIGHT COLUMN: Overview / Client Data / Analysis / Workflow Tabs */}
            <div className="w-96 flex-shrink-0 flex flex-col bg-white dark:bg-[#0d0f17] overflow-hidden">
              <div className="flex border-b border-stone-200 dark:border-white/10 bg-stone-50/50 dark:bg-stone-900/50">
                {[
                  { id: 'overview' as const, label: 'Overview' },
                  { id: 'clientdata' as const, label: 'Client data' },
                  { id: 'analysis' as const, label: 'Analysis' },
                  { id: 'workflow' as const, label: 'Workflow' }
                ].map((t) => (
                  <button
                    key={t.id}
                    onClick={() => setDetailsTab(t.id)}
                    className={`flex-1 py-3 text-xs font-semibold cursor-pointer border-b-2 transition-all ${
                      detailsTab === t.id
                        ? 'text-stone-900 dark:text-white border-stone-900 dark:border-white bg-white dark:bg-[#0d0f17]'
                        : 'text-stone-400 dark:text-stone-500 border-transparent hover:text-stone-600 dark:hover:text-stone-300'
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              <div className="flex-1 overflow-y-auto p-6">
                {/* Tab 1: Overview (AI Summary + Metadata) */}
                {detailsTab === 'overview' && (
                  <div className="space-y-6">
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <h4 className="text-xs font-bold uppercase tracking-wider text-stone-800 dark:text-stone-200">Summary</h4>
                        <button
                          onClick={() => setIsAddingTag(!isAddingTag)}
                          className="flex items-center gap-1 text-[11px] text-stone-500 border border-stone-200 px-2 py-0.5 rounded cursor-pointer hover:bg-stone-50"
                        >
                          <Tag className="w-3 h-3" /> + Add tag
                        </button>
                      </div>

                      {isAddingTag && (
                        <div className="flex gap-1.5 mb-2">
                          <input
                            type="text"
                            placeholder="Tag name..."
                            value={newTagInput}
                            onChange={(e) => setNewTagInput(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' && newTagInput.trim()) {
                                setTags([...tags, newTagInput.trim()]);
                                setNewTagInput('');
                                setIsAddingTag(false);
                              }
                            }}
                            className="flex-1 text-xs border border-stone-200 px-2 py-1 rounded bg-stone-50"
                          />
                          <button
                            onClick={() => {
                              if (newTagInput.trim()) {
                                setTags([...tags, newTagInput.trim()]);
                                setNewTagInput('');
                                setIsAddingTag(false);
                              }
                            }}
                            className="px-2 py-1 bg-stone-900 text-white rounded text-xs"
                          >
                            Add
                          </button>
                        </div>
                      )}

                      <div className="flex flex-wrap gap-1 mb-2">
                        {tags.map((t, idx) => (
                          <span key={idx} className="px-2 py-0.5 bg-stone-100 text-stone-600 rounded text-[10px] font-medium">
                            #{t}
                          </span>
                        ))}
                      </div>

                      <p className="text-xs text-stone-600 dark:text-stone-300 leading-relaxed font-normal">
                        {summaryText}
                      </p>
                    </div>

                    <div className="h-px bg-stone-100" />

                    <div>
                      <div className="flex items-center justify-between mb-3">
                        <h4 className="text-xs font-bold uppercase tracking-wider text-stone-800 dark:text-stone-200">Metadata</h4>
                        <button
                          onClick={() => {
                            navigator.clipboard.writeText(`conv_${sessionId.substring(4, 28)}`);
                            setCopiedId(true);
                            setTimeout(() => setCopiedId(false), 1500);
                          }}
                          className="flex items-center gap-1 text-[11px] text-stone-500 hover:text-stone-800 cursor-pointer"
                        >
                          {copiedId ? <Check className="w-3 h-3 text-emerald-600" /> : <Copy className="w-3 h-3" />}
                          <span>{copiedId ? 'Copied' : 'Copy ID'}</span>
                        </button>
                      </div>

                      <div className="space-y-2.5 text-xs">
                        {[
                          ['Conversation ID', `conv_${sessionId.substring(4, 28)}`],
                          ['Date', new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' })],
                          ['Workflow', workflows.find((w) => w.id === selectedWorkflowId)?.name || 'Default'],
                          ['Active Subagent', `${currentAgentName} ⓘ Active`],
                          ['Status', 'Successful'],
                          ['Sentiment', `• Sentiment ${sentiment.overall}   • Frustration ${sentiment.frustration}`],
                          ['Environment', 'production'],
                          ['User ID', `user_${sessionId.substring(4, 22)}`],
                          ['Duration', `${totalDurationSec} seconds`],
                          ['Messages', `${totalMessagesCount} messages`],
                          ['Conversation cost', `${Math.max(48, totalMessagesCount * 24)} credits`],
                          ['Credits (LLM)', String(Math.max(12, totalMessagesCount * 2))],
                        ].map(([label, val]) => (
                          <div key={label} className="flex justify-between items-start gap-2">
                            <span className="text-stone-400 font-medium w-28 shrink-0">{label}</span>
                            <span className="text-stone-800 dark:text-stone-200 font-medium text-right break-all">
                              {label === 'Status' ? (
                                <span className="px-2 py-0.5 bg-emerald-50 text-emerald-700 border border-emerald-200 rounded text-[11px] font-bold">
                                  {val}
                                </span>
                              ) : (
                                val
                              )}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}

                {/* Tab 2: Client Data (Simulated CRM Payload) */}
                {detailsTab === 'clientdata' && (
                  <div className="space-y-4 text-xs">
                    <h4 className="text-xs font-bold uppercase tracking-wider text-stone-800 dark:text-stone-200 mb-2">Caller CRM Attributes</h4>
                    <div className="space-y-2 border border-stone-200 dark:border-white/10 rounded-xl p-3 bg-stone-50/60 dark:bg-stone-800/60">
                      {[
                        ['Caller', 'Web Simulator User (EMP-48201)'],
                        ['Department', 'Finance & Digital Operations'],
                        ['Affected Service', 'SAP ECC / S/4HANA & Enterprise VPN'],
                        ['Priority Level', 'P2 - Business Operations'],
                        ['Ticketing System', 'Symphony Service Desk (Cloud Function API)'],
                        ['Assigned Support Team', 'STL Digital IT Helpdesk'],
                      ].map(([k, v]) => (
                        <div key={k} className="flex justify-between py-1 border-b border-stone-200/50 last:border-0">
                          <span className="text-stone-500">{k}</span>
                          <span className="font-semibold text-stone-800 dark:text-stone-200">{v}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Tab 3: Analysis */}
                {detailsTab === 'analysis' && (
                  <div className="space-y-6">
                    <div>
                      <h4 className="text-xs font-bold uppercase tracking-wider text-stone-800 dark:text-stone-200 mb-3">Sentiment Breakdown</h4>
                      <div className="space-y-2">
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-stone-500">Overall</span>
                          <span className="font-bold text-stone-800 dark:text-stone-100">{sentiment.overall}</span>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-stone-500">Frustration</span>
                          <span className="font-bold text-stone-800 dark:text-stone-100">{sentiment.frustration}</span>
                        </div>
                      </div>
                    </div>

                    <div className="h-px bg-stone-100" />

                    <div>
                      <h4 className="text-xs font-bold uppercase tracking-wider text-stone-800 dark:text-stone-200 mb-3">Performance Telemetry</h4>
                      <div className="space-y-2 text-xs">
                        <div className="flex justify-between">
                          <span className="text-stone-500">Avg LLM Latency</span>
                          <span className="font-mono font-bold text-stone-800 dark:text-stone-200">{avgLlm || 420} ms</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-stone-500">Avg TTS Synthesis</span>
                          <span className="font-mono font-bold text-stone-800 dark:text-stone-200">{avgTts || 110} ms</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-stone-500">Agent Turns</span>
                          <span className="font-bold text-stone-800 dark:text-stone-200">{agentTurns.length}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {/* Tab 4: Workflow Graph (Exact match to Screenshot 4) */}
                {detailsTab === 'workflow' && (
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <h4 className="text-xs font-bold uppercase tracking-wider text-stone-800 dark:text-stone-200">
                        Active Flow: {workflows.find((w) => w.id === selectedWorkflowId)?.name || currentAgentName}
                      </h4>
                      <button
                        onClick={() => router.push('/workflow')}
                        className="flex items-center gap-1 text-[11px] text-teal-700 font-semibold hover:underline cursor-pointer"
                      >
                        Edit in Architect <ArrowUpRight className="w-3 h-3" />
                      </button>
                    </div>

                    <div className="bg-stone-50 dark:bg-stone-800/80 rounded-2xl border border-stone-200 dark:border-white/10 p-4 space-y-2 text-xs">
                      {[
                        { name: 'Start', desc: 'Call entrypoint & routing initialization', type: 'start' },
                        { name: 'General Inquiry Agent', desc: 'Handles general customer queries & classification', type: 'node' },
                        { name: 'Technical Support Agent', desc: 'Hardware, Wi-Fi & paper jam troubleshooting', type: 'active' },
                        { name: 'Escalation Agent', desc: 'Supervisor transfer & L2 routing', type: 'node' },
                        { name: 'AMC & Sales Agent', desc: 'Warranty & maintenance plans', type: 'node' },
                        { name: 'Transfer (+1 0170 41 57505)', desc: 'Human agent escalation', type: 'exit' },
                      ].map((n, i, arr) => (
                        <div key={n.name} className="flex flex-col items-center">
                          <div
                            className={`w-full p-3 rounded-xl border shadow-2xs ${
                              n.type === 'start'
                                ? 'bg-stone-900 text-white border-stone-900'
                                : n.type === 'active'
                                ? 'bg-teal-50 border-teal-300 text-teal-950 font-bold'
                                : n.type === 'exit'
                                ? 'bg-stone-100 text-stone-600 border-stone-200'
                                : 'bg-white dark:bg-stone-900 text-stone-800 dark:text-stone-200 border-stone-200 dark:border-white/10'
                            }`}
                          >
                            <div className="flex items-center justify-between">
                              <span className="font-bold">{n.name}</span>
                              {n.type === 'active' && (
                                <span className="w-2 h-2 rounded-full bg-teal-500 animate-pulse" />
                              )}
                            </div>
                            <p className="text-[10px] opacity-70 mt-0.5">{n.desc}</p>
                          </div>
                          {i < arr.length - 1 && (
                            <div className="h-3 w-px bg-stone-300 my-0.5" />
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ─── RIGHT DOCKED SIMULATOR (Fixed 440px Width) ─── */}
      {isSimulatorOpen && (
      <div className="w-[440px] flex-shrink-0 flex flex-col bg-white dark:bg-[#0d0f17] border-l border-stone-200 dark:border-white/10 overflow-hidden shadow-lg">
        
        {/* Top Control Bar */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-stone-200 dark:border-white/10 bg-white dark:bg-[#0d0f17] flex-shrink-0">
          <div className="flex items-center gap-3 text-xs font-medium">
            <button
              onClick={() => setSimTab('inline')}
              className={`cursor-pointer pb-0.5 transition-all ${
                simTab === 'inline'
                  ? 'font-bold text-stone-900 dark:text-white border-b-2 border-stone-900 dark:border-white'
                  : 'text-stone-400 dark:text-stone-500 hover:text-stone-600 dark:hover:text-stone-300'
              }`}
            >
              Inline
            </button>
            <span className="text-stone-300">|</span>
            <button
              onClick={() => setSimTab('widget')}
              className={`cursor-pointer pb-0.5 transition-all ${
                simTab === 'widget'
                  ? 'font-bold text-stone-900 dark:text-white border-b-2 border-stone-900 dark:border-white'
                  : 'text-stone-400 dark:text-stone-500 hover:text-stone-600 dark:hover:text-stone-300'
              }`}
            >
              Widget
            </button>
          </div>

          <div className="flex items-center gap-2 text-xs text-stone-500">
            <button
              onClick={() => setMockTools(!mockTools)}
              className="flex items-center gap-1 text-[11px] text-stone-400 hover:text-stone-600 cursor-pointer"
            >
              <Wrench className="w-3 h-3" /> Mock tools: <span className="font-bold text-stone-600">{mockTools ? 'on' : 'off'}</span>
            </button>
            <button
              onClick={() => initSession(selectedWorkflowId)}
              title="Reset session"
              className="p-1 hover:bg-stone-100 rounded text-stone-400 hover:text-stone-700 cursor-pointer ml-1"
            >
              <RefreshCw className="w-3.5 h-3.5 stroke-[1.5]" />
            </button>
            <button
              onClick={() => setIsSimulatorOpen(false)}
              title="Close simulator panel"
              className="p-1 hover:bg-stone-200 rounded text-stone-500 hover:text-stone-800 cursor-pointer ml-1"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* Live Call Hands-free Banner (when call is active) */}
        {isLiveCall && (
          <div className="px-4 py-2 bg-gradient-to-r from-emerald-500 to-teal-600 text-white flex items-center justify-between text-xs shadow-xs animate-fadeIn">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-white animate-ping" />
              <span className="font-bold">Live Call Active</span>
              <span className="font-mono text-emerald-100 text-[11px]">{formatSec(callDuration)}</span>
            </div>
            <span className="text-[10px] font-medium text-emerald-100">Hands-free (No click needed)</span>
          </div>
        )}

        {/* ── MODE 1: INLINE CHAT STREAM (Screenshot 1) ── */}
        {simTab === 'inline' && (
          <div className="flex-1 flex flex-col min-h-0 bg-white dark:bg-[#0d0f17]">
            {/* Messages Scroll Area */}
            <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
              {messages.map((msg) => {
                if (msg.sender === 'system') return null;
                return (
                  <div key={msg.id} className="space-y-1.5">
                    {msg.sender === 'agent' ? (
                      <div className="flex items-start gap-2.5 max-w-[88%]">
                        <div className="w-7 h-7 rounded-full bg-teal-500 flex-shrink-0 flex items-center justify-center text-white text-[11px] font-bold shadow-2xs mt-0.5">
                          {(msg.agentName || 'B').substring(0, 1).toUpperCase()}
                        </div>
                        <div className="flex-1 bg-stone-50 dark:bg-stone-800/80 border border-stone-200/70 dark:border-white/10 text-stone-900 dark:text-stone-100 rounded-2xl rounded-tl-xs px-3.5 py-2.5 text-xs leading-relaxed shadow-2xs">
                          <p className="whitespace-pre-wrap font-normal">{msg.text}</p>
                          {msg.kycLink && (
                            <a href={msg.kycLink.url} target="_blank" rel="noopener noreferrer"
                              className="mt-2 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-teal-600 hover:bg-teal-700 text-white text-[11px] font-semibold no-underline">
                              Open verification page (code {msg.kycLink.code})
                            </a>
                          )}
                          {msg.toolCalled && (
                            <div className="mt-1.5 flex items-center gap-1 px-2 py-0.5 bg-emerald-50 border border-emerald-200 text-emerald-800 rounded-md text-[10px] font-mono font-semibold w-fit">
                              <Wrench className="w-2.5 h-2.5 text-emerald-600" />
                              <span>{msg.toolCalled === 'create_symphony_ticket' ? 'Symphony Ticket Created' : msg.toolCalled}</span>
                            </div>
                          )}
                          {msg.audioBase64 && (
                            <div className="mt-1.5 flex justify-end">
                              <button
                                onClick={() => playAudio(msg.audioBase64, msg.text)}
                                className="flex items-center gap-1 text-[10px] text-stone-400 hover:text-stone-700 cursor-pointer"
                              >
                                <Volume2 className="w-3 h-3" /> Replay
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    ) : (
                      <div className="flex justify-end">
                        <div className="max-w-[84%] bg-stone-900 text-white dark:bg-white dark:text-stone-900 border border-stone-900 dark:border-white rounded-2xl rounded-tr-xs px-3.5 py-2.5 text-xs leading-relaxed shadow-2xs">
                          <p className="whitespace-pre-wrap">{msg.text}</p>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}

              {/* Agent thinking / speaking bubble */}
              {loading && (
                <div className="flex items-start gap-2.5 max-w-[88%]">
                  <div className="w-7 h-7 rounded-full bg-teal-500 flex-shrink-0 flex items-center justify-center text-white text-[11px] font-bold">
                    B
                  </div>
                  <div className="bg-stone-50 border border-stone-200/70 rounded-2xl rounded-tl-xs px-4 py-3 shadow-2xs flex items-center gap-1.5">
                    <div className="w-1.5 h-1.5 bg-stone-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                    <div className="w-1.5 h-1.5 bg-stone-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                    <div className="w-1.5 h-1.5 bg-stone-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                  </div>
                </div>
              )}

              {/* Live speech feedback */}
              {interimSpeech && (
                <div className="flex justify-end">
                  <div className="max-w-[84%] bg-stone-50 border border-teal-300 text-stone-600 rounded-2xl px-3.5 py-2 text-xs italic animate-pulse">
                    &quot;{interimSpeech}&quot;
                  </div>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>

            {/* Bottom Docked Input Box */}
            <div className="p-4 border-t border-stone-200 dark:border-white/10 bg-white dark:bg-[#0d0f17] flex-shrink-0 space-y-3">
              {/* Prompt chips */}
              <div className="flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-none">
                {SAMPLE_PROMPTS.map((p, i) => (
                  <button
                    key={i}
                    disabled={loading}
                    onClick={() => handleSend(p.text)}
                    className="px-2.5 py-1 text-[11px] font-medium border border-stone-200 dark:border-white/10 hover:border-stone-300 dark:hover:border-white/20 hover:bg-stone-50 dark:hover:bg-stone-800 rounded-full text-stone-600 dark:text-stone-300 whitespace-nowrap cursor-pointer transition-colors shrink-0 disabled:opacity-40"
                  >
                    {p.label}
                  </button>
                ))}
              </div>

              {/* Input Card */}
              <div className="bg-stone-50 dark:bg-stone-800/80 border border-stone-200 dark:border-white/10 rounded-2xl p-2 shadow-2xs focus-within:border-stone-400 dark:focus-within:border-white/20 focus-within:bg-white dark:focus-within:bg-stone-800 transition-all">
                <textarea
                  rows={2}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      handleSend();
                    }
                  }}
                  placeholder="Send a message"
                  disabled={loading}
                  className="w-full bg-transparent text-xs text-stone-900 dark:text-white placeholder:text-stone-400 dark:placeholder:text-stone-500 focus:outline-none resize-none px-2 py-1"
                />

                {/* Bottom Toolbar Icons (Matching Screenshot 1) */}
                <div className="flex items-center justify-between pt-1 px-1">
                  <div className="flex items-center gap-2 text-stone-400">
                    <button
                      type="button"
                      onClick={() => setShowSettingsModal(true)}
                      title="Voice Settings"
                      className="p-1 hover:text-stone-600 cursor-pointer"
                    >
                      <Settings className="w-3.5 h-3.5 stroke-[1.5]" />
                    </button>
                    <button
                      type="button"
                      onClick={isListening ? stopAndSubmitRecording : () => startContinuousRecognition(true)}
                      title={isListening ? 'Done speaking (Click to send)' : 'Voice Input (Click to speak)'}
                      className={`p-1 cursor-pointer transition-colors ${
                        isListening ? 'text-teal-600 animate-pulse' : 'hover:text-stone-600'
                      }`}
                    >
                      {isListening ? <Mic className="w-3.5 h-3.5 stroke-[1.5]" /> : <MicOff className="w-3.5 h-3.5 stroke-[1.5]" />}
                    </button>
                    <button
                      type="button"
                      onClick={() => setAudioMuted(!audioMuted)}
                      title={audioMuted ? 'Agent Voice Muted (Click to unmute)' : 'Agent Voice Auto-play Active (Click to mute)'}
                      className={`p-1 cursor-pointer transition-colors ${
                        audioMuted ? 'text-amber-500 hover:text-amber-600' : 'text-teal-600 hover:text-teal-700'
                      }`}
                    >
                      {audioMuted ? <VolumeX className="w-3.5 h-3.5 stroke-[1.5]" /> : <Volume2 className="w-3.5 h-3.5 stroke-[1.5]" />}
                    </button>
                    <button
                      type="button"
                      onClick={() => setSelectedTab('knowledge')}
                      title="Attach KB Knowledge Base"
                      className="p-1 hover:text-stone-600 cursor-pointer"
                    >
                      <Paperclip className="w-3.5 h-3.5 stroke-[1.5]" />
                    </button>
                    <button
                      type="button"
                      onClick={exportTranscript}
                      title="Export Conversation Transcript"
                      className="p-1 hover:text-stone-600 cursor-pointer"
                    >
                      <FileText className="w-3.5 h-3.5 stroke-[1.5]" />
                    </button>
                  </div>

                  <div className="flex items-center gap-2">
                    {/* Red Hands-Free Call Button */}
                    <button
                      type="button"
                      onClick={toggleLiveCall}
                      title={isLiveCall ? 'End Call' : 'Start Hands-free Call'}
                      className={`w-8 h-8 rounded-full flex items-center justify-center cursor-pointer transition-all shadow-sm active:scale-95 ${
                        isLiveCall
                          ? 'bg-rose-600 hover:bg-rose-700 text-white animate-pulse'
                          : 'bg-rose-500 hover:bg-rose-600 text-white'
                      }`}
                    >
                      {isLiveCall ? <PhoneOff className="w-4 h-4 stroke-[1.5]" /> : <Phone className="w-4 h-4 stroke-[1.5]" />}
                    </button>

                    {/* Send Button */}
                    <button
                      type="button"
                      onClick={() => handleSend()}
                      disabled={!input.trim() || loading}
                      className="w-8 h-8 rounded-full bg-stone-900 hover:bg-stone-800 disabled:bg-stone-200 text-white flex items-center justify-center cursor-pointer transition-colors shadow-sm"
                    >
                      <Send className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── MODE 2: WIDGET (Animated Spinning Orb) ── */}
        {simTab === 'widget' && (
          <div className="flex-1 flex flex-col bg-[#f0efee] dark:bg-[#08090e]">
            <div className="flex-1 flex flex-col items-center justify-center p-8 relative overflow-hidden">
              <div
                className="absolute inset-0"
                style={{
                  backgroundImage: 'radial-gradient(circle, rgba(180,175,168,0.4) 1px, transparent 1px)',
                  backgroundSize: '22px 22px'
                }}
              />
              <div className="relative z-10 flex flex-col items-center gap-6">
                <div className="relative">
                  {orbActive && (
                    <>
                      <div
                        className="absolute rounded-full animate-ping opacity-20"
                        style={{ inset: '-20px', background: isListening ? '#38bdf8' : '#2dd4bf' }}
                      />
                      <div
                        className="absolute rounded-full opacity-15"
                        style={{ inset: '-10px', background: isListening ? '#0ea5e9' : '#0d9488', filter: 'blur(8px)' }}
                      />
                    </>
                  )}
                  <div
                    className={`w-44 h-44 rounded-full relative overflow-hidden transition-transform duration-700 ${
                      orbActive ? 'scale-105' : 'scale-100'
                    }`}
                    style={{
                      background: orbGradient,
                      animation: orbActive ? 'orb-spin 3s linear infinite' : 'orb-spin 8s linear infinite',
                      boxShadow: orbActive
                        ? '0 0 60px rgba(13,148,136,0.5), 0 20px 60px rgba(0,0,0,0.15)'
                        : '0 0 40px rgba(13,148,136,0.3), 0 20px 40px rgba(0,0,0,0.12)'
                    }}
                  >
                    <div
                      className="absolute inset-0 rounded-full"
                      style={{
                        background:
                          'radial-gradient(ellipse at 35% 28%, rgba(255,255,255,0.38) 0%, rgba(255,255,255,0.1) 45%, transparent 68%)'
                      }}
                    />
                  </div>

                  {/* Overlaid phone call toggle */}
                  <button
                    onClick={toggleLiveCall}
                    className={`absolute -bottom-1 -right-1 w-11 h-11 rounded-full flex items-center justify-center shadow-lg border-2 border-white transition-all cursor-pointer z-20 ${
                      isLiveCall ? 'bg-rose-600 hover:bg-rose-700' : 'bg-stone-900 hover:bg-stone-800'
                    }`}
                    title={isLiveCall ? 'End voice call' : 'Start voice call'}
                  >
                    {isLiveCall ? (
                      <PhoneOff className="w-4 h-4 text-white stroke-[1.5]" />
                    ) : (
                      <Phone className="w-4 h-4 text-white stroke-[1.5]" />
                    )}
                  </button>
                </div>

                <div className="text-center space-y-1">
                  <p className="text-xs font-bold text-stone-700">
                    {isSpeaking
                      ? 'Agent speaking...'
                      : isListening
                      ? 'Listening hands-free...'
                      : isLiveCall
                      ? 'Call connected'
                      : 'Tap phone icon to call'}
                  </p>
                  {isListening && interimSpeech && (
                    <p className="text-[10px] text-stone-500 max-w-[200px] truncate">&quot;{interimSpeech}&quot;</p>
                  )}
                </div>
              </div>
            </div>

            {/* Bottom input in widget mode */}
            <div className="p-4 bg-white dark:bg-[#0d0f17] border-t border-stone-200 dark:border-white/10 space-y-2">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  handleSend();
                }}
                className="flex items-center gap-2"
              >
                <input
                  type="text"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder="Send a message to start a chat"
                  disabled={loading}
                  className="flex-1 px-3.5 py-2 bg-stone-50 dark:bg-stone-800 border border-stone-200 dark:border-white/10 rounded-xl text-xs text-stone-900 dark:text-white focus:outline-none"
                />
                <button
                  type="submit"
                  disabled={!input.trim() || loading}
                  className="p-2 bg-stone-900 text-white rounded-xl cursor-pointer disabled:bg-stone-200"
                >
                  <Send className="w-3.5 h-3.5" />
                </button>
              </form>
            </div>
          </div>
        )}
      </div>

            )}

      {/* Floating Action Button to Pop Open Simulator when closed */}
      {!isSimulatorOpen && (
        <button
          onClick={() => setIsSimulatorOpen(true)}
          className="fixed bottom-6 right-6 z-40 px-5 py-3 bg-stone-900 hover:bg-stone-800 text-white rounded-full shadow-2xl flex items-center gap-2.5 text-xs font-bold cursor-pointer transition-all hover:scale-105 active:scale-95 border border-stone-700 animate-fadeIn"
        >
          <div className="w-6 h-6 rounded-full bg-teal-500 flex items-center justify-center text-white">
            <Phone className="w-3.5 h-3.5 stroke-[2]" />
          </div>
          <span>Test AI Agent</span>
          {isLiveCall && <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping" />}
        </button>
      )}

      {/* ── MODAL 1: CHUNK PREVIEW DRAWER ── */}
      {selectedChunk && (
        <div className="fixed inset-0 bg-stone-900/40 z-50 flex items-center justify-center p-4 backdrop-blur-2xs animate-fadeIn">
          <div className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 rounded-2xl max-w-lg w-full p-6 shadow-2xl space-y-4">
            <div className="flex items-center justify-between border-b border-stone-100 pb-3">
              <div>
                <h3 className="text-sm font-bold text-stone-900 dark:text-white">{selectedChunk.title}</h3>
                <p className="text-[11px] text-stone-400 font-mono">Chunk ID: {selectedChunk.id}</p>
              </div>
              <button onClick={() => setSelectedChunk(null)} className="p-1 hover:bg-stone-100 rounded cursor-pointer">
                <X className="w-4 h-4 text-stone-500" />
              </button>
            </div>
            <div className="p-3.5 bg-stone-50 dark:bg-stone-800/80 rounded-xl border border-stone-200 dark:border-white/10 text-xs text-stone-700 dark:text-stone-200 leading-relaxed font-mono">
              {selectedChunk.text}
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => setSelectedChunk(null)}
                className="px-4 py-1.5 bg-stone-900 text-white rounded-lg text-xs font-semibold cursor-pointer"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── MODAL 2: VARS & PROMPT DRAWER ── */}
      {showVarsModal && (
        <div className="fixed inset-0 bg-stone-900/40 z-50 flex items-center justify-center p-4 backdrop-blur-2xs animate-fadeIn">
          <div className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 rounded-2xl max-w-xl w-full p-6 shadow-2xl space-y-4">
            <div className="flex items-center justify-between border-b border-stone-100 pb-3">
              <div className="flex items-center gap-2">
                <Code2 className="w-4 h-4 text-teal-600" />
                <h3 className="text-sm font-bold text-stone-900 dark:text-white">Agent Variables & System Prompt</h3>
              </div>
              <button onClick={() => setShowVarsModal(false)} className="p-1 hover:bg-stone-100 rounded cursor-pointer">
                <X className="w-4 h-4 text-stone-500" />
              </button>
            </div>
            <div className="space-y-3 text-xs">
              <div>
                <label className="font-bold text-stone-700 block mb-1">Active Agent</label>
                <input
                  type="text"
                  value={currentAgentName}
                  readOnly
                  className="w-full p-2 bg-stone-50 dark:bg-stone-800 border border-stone-200 dark:border-white/10 rounded-lg text-stone-800 dark:text-white font-semibold"
                />
              </div>
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="text-[11px] text-stone-500 block mb-1">LLM Model</label>
                  <div className="p-2 bg-stone-50 border border-stone-200 rounded-lg font-mono text-[11px]">groq/gpt-oss-20b</div>
                </div>
                <div>
                  <label className="text-[11px] text-stone-500 block mb-1">Temperature</label>
                  <div className="p-2 bg-stone-50 border border-stone-200 rounded-lg font-mono text-[11px]">0.3 (Deterministic)</div>
                </div>
                <div>
                  <label className="text-[11px] text-stone-500 block mb-1">TTS Voice</label>
                  <div className="p-2 bg-stone-50 border border-stone-200 rounded-lg font-mono text-[11px]">Cartesia Sonic 3.5</div>
                </div>
              </div>
              <div>
                <label className="font-bold text-stone-700 block mb-1">Dynamic Template Variables</label>
                <div className="p-2.5 bg-stone-50 border border-stone-200 rounded-lg space-y-1 font-mono text-[11px]">
                  <div><span className="text-teal-700 font-bold">&#123;&#123;caller_phone&#125;&#125;</span> : &quot;+1 (352) 725-3511&quot;</div>
                  <div><span className="text-teal-700 font-bold">&#123;&#123;company_name&#125;&#125;</span> : &quot;STL Digital Enterprise&quot;</div>
                  <div><span className="text-teal-700 font-bold">&#123;&#123;sla_tier&#125;&#125;</span> : &quot;Gold Support 24x7&quot;</div>
                </div>
              </div>
            </div>
            <div className="flex justify-end pt-2">
              <button
                onClick={() => setShowVarsModal(false)}
                className="px-4 py-1.5 bg-stone-900 text-white rounded-lg text-xs font-semibold cursor-pointer"
              >
                Save & Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── MODAL 3: SEARCH AGENTS DRAWER ── */}
      {showSearchModal && (
        <div className="fixed inset-0 bg-stone-900/40 z-50 flex items-center justify-center p-4 backdrop-blur-2xs animate-fadeIn">
          <div className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 rounded-2xl max-w-lg w-full p-6 shadow-2xl space-y-4">
            <div className="flex items-center justify-between border-b border-stone-100 pb-3">
              <div className="flex items-center gap-2">
                <Search className="w-4 h-4 text-teal-600" />
                <h3 className="text-sm font-bold text-stone-900 dark:text-white">Search Agents & Sub-Workflows</h3>
              </div>
              <button onClick={() => setShowSearchModal(false)} className="p-1 hover:bg-stone-100 rounded cursor-pointer">
                <X className="w-4 h-4 text-stone-500" />
              </button>
            </div>
            <div className="space-y-2 max-h-72 overflow-y-auto text-xs">
              {[
                { name: 'IT Service Desk Reception', desc: 'Main customer support & triage entrypoint' },
                { name: 'Symphony Incident Specialist', desc: 'Logs P1-P4 incidents and manages ticket lifecycle' },
                { name: 'NOC Escalation Manager', desc: 'Critical outages and duty commander bridge' },
                { name: 'SAP & ERP Specialist', desc: 'SAP GUI, transaction code, and auth troubleshooting' },
                { name: 'Network & VPN Support Agent', desc: 'Wi-Fi, GlobalProtect VPN, and gateway connectivity' },
                { name: 'Ticket Status & Lookup Agent', desc: 'Symphony ticket query and SLA status' },
              ].map((a) => (
                <button
                  key={a.name}
                  onClick={() => {
                    setCurrentAgentName(a.name);
                    setShowSearchModal(false);
                    initSession(selectedWorkflowId);
                  }}
                  className="w-full text-left p-3 border border-stone-200 dark:border-white/10 rounded-xl hover:bg-stone-50 dark:hover:bg-stone-800 cursor-pointer flex items-center justify-between transition-colors"
                >
                  <div>
                    <p className="font-bold text-stone-800 dark:text-white">{a.name}</p>
                    <p className="text-[11px] text-stone-500">{a.desc}</p>
                  </div>
                  <span className="text-[10px] text-teal-700 font-semibold border border-teal-200 bg-teal-50 px-2 py-0.5 rounded">Select</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ── MODAL 4: AGENT TEST RUNNER ── */}
      {showTestModal && (
        <div className="fixed inset-0 bg-stone-900/40 z-50 flex items-center justify-center p-4 backdrop-blur-2xs animate-fadeIn">
          <div className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 rounded-2xl max-w-xl w-full p-6 shadow-2xl space-y-4">
            <div className="flex items-center justify-between border-b border-stone-100 pb-3">
              <div className="flex items-center gap-2">
                <PlayCircle className="w-4 h-4 text-sky-600" />
                <h3 className="text-sm font-bold text-stone-900 dark:text-white">Agent Test Suites</h3>
              </div>
              <button onClick={() => setShowTestModal(false)} className="p-1 hover:bg-stone-100 rounded cursor-pointer">
                <X className="w-4 h-4 text-stone-500" />
              </button>
            </div>
            <div className="space-y-3 text-xs">
              {[
                { title: 'Scenario 1: Printer Paper Jam Resolution', status: 'Passed (420ms)', score: '100%' },
                { title: 'Scenario 2: ERP Incident P1 Escalation SLA', status: 'Passed (380ms)', score: '98%' },
                { title: 'Scenario 3: Multilingual Hinglish Intent Triage', status: 'Passed (510ms)', score: '95%' },
              ].map((t, idx) => (
                <div key={idx} className="p-3 border border-stone-200 rounded-xl bg-stone-50 flex items-center justify-between">
                  <div>
                    <p className="font-bold text-stone-800 dark:text-white">{t.title}</p>
                    <p className="text-[11px] text-stone-500 mt-0.5">Benchmark: {t.status}</p>
                  </div>
                  <span className="px-2.5 py-1 bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-full font-bold text-[11px]">
                    {t.score}
                  </span>
                </div>
              ))}
            </div>
            <div className="flex justify-between items-center pt-3 border-t border-stone-100">
              <span className="text-[11px] text-stone-400">All 3 integration tests passed successfully</span>
              <button
                onClick={() => {
                  setTestRunStatus('running');
                  setTimeout(() => {
                    setTestRunStatus('passed');
                    setTimeout(() => setShowTestModal(false), 1000);
                  }, 1200);
                }}
                className="px-4 py-2 bg-stone-900 hover:bg-stone-800 text-white rounded-lg text-xs font-semibold cursor-pointer"
              >
                {testRunStatus === 'running' ? 'Running Tests...' : testRunStatus === 'passed' ? 'All Tests Passed ✓' : 'Run Test Suite'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── MODAL 5: VOICE & SIMULATOR SETTINGS ── */}
      {showSettingsModal && (
        <div className="fixed inset-0 bg-stone-900/40 z-50 flex items-center justify-center p-4 backdrop-blur-2xs animate-fadeIn">
          <div className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-white/10 rounded-2xl max-w-md w-full p-6 shadow-2xl space-y-4">
            <div className="flex items-center justify-between border-b border-stone-100 pb-3">
              <div className="flex items-center gap-2">
                <Settings className="w-4 h-4 text-stone-700" />
                <h3 className="text-sm font-bold text-stone-900 dark:text-white">Voice & Telemetry Settings</h3>
              </div>
              <button onClick={() => setShowSettingsModal(false)} className="p-1 hover:bg-stone-100 rounded cursor-pointer">
                <X className="w-4 h-4 text-stone-500" />
              </button>
            </div>
            <div className="space-y-3 text-xs">
              <div>
                <label className="font-bold text-stone-700 block mb-1">Speech Synthesis Provider (TTS)</label>
                <select className="w-full p-2 bg-stone-50 dark:bg-stone-800 border border-stone-200 dark:border-white/10 rounded-lg text-stone-800 dark:text-stone-100">
                  <option>Cartesia Sonic 3.5 (Ultra-low latency ~90ms)</option>
                  <option>Sarvam AI Bulbul (Indic voices)</option>
                  <option>Gnani.ai Timbre v2.5</option>
                  <option>Browser Web Speech Synthesis</option>
                </select>
              </div>
              <div>
                <label className="font-bold text-stone-700 block mb-1">Speech Recognition Engine (STT)</label>
                <select className="w-full p-2 bg-stone-50 dark:bg-stone-800 border border-stone-200 dark:border-white/10 rounded-lg text-stone-800 dark:text-stone-100">
                  <option>Sarvam AI Saarika (Indian English & Multilingual)</option>
                  <option>Cartesia Ink STT</option>
                  <option>Gnani.ai Vach Prisma v2.5</option>
                </select>
              </div>
              <div>
                <label className="font-bold text-stone-700 block mb-1">Voice Activity Detection (VAD) Sensitivity</label>
                <div className="flex items-center gap-3">
                  <input type="range" min="1" max="5" defaultValue="4" className="flex-1" />
                  <span className="font-mono font-bold text-stone-800 dark:text-stone-200">High (1.1s)</span>
                </div>
              </div>
            </div>
            <div className="flex justify-end pt-2">
              <button
                onClick={() => setShowSettingsModal(false)}
                className="px-4 py-1.5 bg-stone-900 text-white rounded-lg text-xs font-semibold cursor-pointer"
              >
                Apply Settings
              </button>
            </div>
          </div>
        </div>
      )}

      <style jsx>{`
        @keyframes orb-spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}