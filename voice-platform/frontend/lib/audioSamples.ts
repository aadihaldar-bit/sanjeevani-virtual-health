// Helper for real audio synthesis and friendly preview phrases

export interface VoicePersona {
  id: string;
  name: string;
  category: 'agents' | 'specialists' | 'apis';
  tagline: string;
  description: string;
  voiceName: string;
  sampleText: string;
  sphereGradient: string;
  avatarGradient: string;
  glowColor: string;
  features: string[];
}

export const VOICE_STUDIO_PERSONAS: VoicePersona[] = [
  {
    id: 'studio-characters',
    name: 'Characters',
    category: 'agents',
    tagline: 'Playful, expressive character voices for animations, games, and avatars.',
    description: 'Playful, expressive character voices for animations, games, and avatars.',
    voiceName: 'Shweta',
    sampleText: "Welcome to STL Voice Studio! I am tuned for dynamic character voices and gaming dialogues.",
    sphereGradient: 'radial-gradient(circle at 35% 30%, #e6fff4 0%, #6ee7b7 40%, #0d9488 75%, #042f2e 100%)',
    avatarGradient: 'from-teal-300 via-emerald-400 to-cyan-600',
    glowColor: 'rgba(20, 184, 166, 0.45)',
    features: ['Character Dubbing', 'Emotion Tuning', 'Expressive Timbre'],
  },
  {
    id: 'studio-narration',
    name: 'Narration',
    category: 'agents',
    tagline: 'Deep, rich voices that bring corporate audio, e-learning, and audiobooks to life.',
    description: 'Deep, rich voices that bring corporate audio, e-learning, and audiobooks to life.',
    voiceName: 'Radhika',
    sampleText: "In todays enterprise ecosystem, seamless AI voice interactions transform customer satisfaction.",
    sphereGradient: 'radial-gradient(circle at 35% 30%, #fdf4ff 0%, #d946ef 35%, #8b5cf6 65%, #312e81 100%)',
    avatarGradient: 'from-purple-400 via-fuchsia-500 to-indigo-700',
    glowColor: 'rgba(217, 70, 239, 0.45)',
    features: ['Long-Form Synthesis', 'Audiobook Clarity', 'Podcasting'],
  },
  {
    id: 'studio-conversational',
    name: 'Conversational',
    category: 'agents',
    tagline: 'Ultra-low latency conversational voices with instant barge-in support.',
    description: 'Ultra-low latency conversational voices with instant barge-in support.',
    voiceName: 'Aarohi',
    sampleText: "Hi there! I can chat naturally in English, Hindi, Tamil, and Telugu with sub-300 millisecond response times.",
    sphereGradient: 'radial-gradient(circle at 35% 30%, #fff7ed 0%, #f97316 35%, #ea580c 65%, #7c2d12 100%)',
    avatarGradient: 'from-amber-400 via-orange-500 to-amber-700',
    glowColor: 'rgba(249, 115, 22, 0.45)',
    features: ['<280ms Latency', 'Barge-In Ready', 'Indian Accents'],
  },
  {
    id: 'studio-social',
    name: 'Social Media',
    category: 'agents',
    tagline: 'High-energy, engaging voices for product announcements and short-form video.',
    description: 'High-energy, engaging voices for product announcements and short-form video.',
    voiceName: 'Hope',
    sampleText: "Check out this next-generation voice AI engine from STL Digital! Experience real-time multi-agent workflows.",
    sphereGradient: 'radial-gradient(circle at 35% 30%, #ecfdf5 0%, #10b981 35%, #059669 70%, #064e3b 100%)',
    avatarGradient: 'from-emerald-400 via-teal-500 to-cyan-700',
    glowColor: 'rgba(16, 185, 129, 0.45)',
    features: ['Viral Content', 'High Energy', 'Short Form Audio'],
  },
  {
    id: 'studio-educational',
    name: 'Educational',
    category: 'agents',
    tagline: 'Clear, articulated, and patient voices for training and technical walkthroughs.',
    description: 'Clear, articulated, and patient voices for training and technical walkthroughs.',
    voiceName: 'Deepak',
    sampleText: "Step one: Verify host connectivity on port 5432. Step two: Execute the failover recovery script.",
    sphereGradient: 'radial-gradient(circle at 35% 30%, #eff6ff 0%, #3b82f6 35%, #1d4ed8 70%, #1e1b4b 100%)',
    avatarGradient: 'from-blue-400 via-indigo-500 to-cyan-600',
    glowColor: 'rgba(59, 130, 246, 0.45)',
    features: ['E-Learning', 'Instructional Clarity', 'Step-by-Step'],
  },
  {
    id: 'studio-ads',
    name: 'Advertisement',
    category: 'agents',
    tagline: 'Persuasive, broadcast-grade audio engineered for brand authority and impact.',
    description: 'Persuasive, broadcast-grade audio engineered for brand authority and impact.',
    voiceName: 'Alloy',
    sampleText: "STL Digital Voice Agent: Enterprise telephony meets generative intelligence. Elevate your call center today.",
    sphereGradient: 'radial-gradient(circle at 35% 30%, #fff1f2 0%, #f43f5e 35%, #e11d48 65%, #881337 100%)',
    avatarGradient: 'from-rose-400 via-red-500 to-pink-700',
    glowColor: 'rgba(244, 63, 94, 0.45)',
    features: ['Broadcast Mastered', 'Brand Persona', 'Dynamic Range'],
  },
];

export const VOICE_AGENTS_PERSONAS: VoicePersona[] = [
  {
    id: 'agent-receptionist',
    name: 'Receptionist Router',
    category: 'specialists',
    tagline: 'Multilingual intent detection, caller authentication, and zero-drop department routing.',
    description: 'Multilingual intent detection, caller authentication, and zero-drop department routing.',
    voiceName: 'Aarohi',
    sampleText: "Welcome to STL Enterprise Support. Please describe your issue, or provide your employee ID to get started.",
    sphereGradient: 'radial-gradient(circle at 35% 30%, #fff7ed 0%, #f97316 35%, #ea580c 65%, #7c2d12 100%)',
    avatarGradient: 'from-amber-400 via-orange-500 to-amber-700',
    glowColor: 'rgba(249, 115, 22, 0.45)',
    features: ['Intent Routing', 'Employee Auth', 'Multi-Language'],
  },
  {
    id: 'agent-brother',
    name: 'Brother Printer SOP',
    category: 'specialists',
    tagline: 'Interactive hardware diagnostics, paper jam clearance, and toner drum replacement SOPs.',
    description: 'Interactive hardware diagnostics, paper jam clearance, and toner drum replacement SOPs.',
    voiceName: 'Radhika',
    sampleText: "For Brother DCP-L2540DW paper jams: Open the front cover, remove the drum unit gently, and extract the jammed sheet.",
    sphereGradient: 'radial-gradient(circle at 35% 30%, #fdf4ff 0%, #d946ef 35%, #8b5cf6 65%, #312e81 100%)',
    avatarGradient: 'from-purple-400 via-fuchsia-500 to-indigo-700',
    glowColor: 'rgba(217, 70, 239, 0.45)',
    features: ['SOP Grounded', 'Hardware Troubleshooting', 'RAG Guided'],
  },
  {
    id: 'agent-database',
    name: 'Database & SAP',
    category: 'specialists',
    tagline: 'Automated SAP HANA deadlocks, PostgreSQL connection pool surges, and password resets.',
    description: 'Automated SAP HANA deadlocks, PostgreSQL connection pool surges, and password resets.',
    voiceName: 'Deepak',
    sampleText: "Detected high query latency on the primary cluster. Terminating blocking PID 4821 and refreshing the connection pool.",
    sphereGradient: 'radial-gradient(circle at 35% 30%, #eff6ff 0%, #3b82f6 35%, #1d4ed8 70%, #1e1b4b 100%)',
    avatarGradient: 'from-blue-400 via-indigo-500 to-cyan-600',
    glowColor: 'rgba(59, 130, 246, 0.45)',
    features: ['SAP HANA Recovery', 'PostgreSQL Diagnostics', 'Zero-Downtime'],
  },
  {
    id: 'agent-symphony',
    name: 'Symphony Ticketing',
    category: 'specialists',
    tagline: 'Real-time Summit REST WCF integration: ticket creation, status lookup, and SLA escalations.',
    description: 'Real-time Summit REST WCF integration: ticket creation, status lookup, and SLA escalations.',
    voiceName: 'Gaurav',
    sampleText: "I found incident SYM-INC-2026-77252. Status is Assigned, Workgroup is Facilities, and SLA resolution target is 4 hours.",
    sphereGradient: 'radial-gradient(circle at 35% 30%, #e6fff4 0%, #6ee7b7 40%, #0d9488 75%, #042f2e 100%)',
    avatarGradient: 'from-teal-300 via-emerald-400 to-cyan-600',
    glowColor: 'rgba(20, 184, 166, 0.45)',
    features: ['Summit REST WCF', 'SLA Tracking', 'Live Incident Lookup'],
  },
  {
    id: 'agent-vpn',
    name: 'VPN & Network Desk',
    category: 'specialists',
    tagline: 'Cisco AnyConnect gateway troubleshooting, Wi-Fi cert renewal, and BitLocker recovery.',
    description: 'Cisco AnyConnect gateway troubleshooting, Wi-Fi cert renewal, and BitLocker recovery.',
    voiceName: 'Hemraj',
    sampleText: "Please open AnyConnect, verify gateway vpn.stltech.in, and I will issue a temporary one-time bypass token.",
    sphereGradient: 'radial-gradient(circle at 35% 30%, #fff1f2 0%, #f43f5e 35%, #e11d48 65%, #881337 100%)',
    avatarGradient: 'from-rose-400 via-red-500 to-pink-700',
    glowColor: 'rgba(244, 63, 94, 0.45)',
    features: ['Cisco AnyConnect', 'BitLocker Retrieval', 'DNS Flush'],
  },
];

export const TELEPHONY_API_PERSONAS: VoicePersona[] = [
  {
    id: 'telephony-smartflo',
    name: 'Tata Smartflo SIP',
    category: 'apis',
    tagline: 'Carrier-grade pilot SIP trunking with 8kHz G.711 mu-law audio streaming.',
    description: 'Carrier-grade pilot SIP trunking with 8kHz G.711 mu-law audio streaming.',
    voiceName: 'Alloy',
    sampleText: "Connected to Tata Teleservices Smartflo Cloud EPABX on DID 918069879488. Streaming bidirectional RTP audio.",
    sphereGradient: 'radial-gradient(circle at 35% 30%, #fff1f2 0%, #f43f5e 35%, #e11d48 65%, #881337 100%)',
    avatarGradient: 'from-rose-400 via-red-500 to-pink-700',
    glowColor: 'rgba(244, 63, 94, 0.45)',
    features: ['8kHz mu-law Audio', 'Tata Smartflo Trunk', 'Webhooks'],
  },
  {
    id: 'telephony-twilio',
    name: 'Twilio Voice Bridge',
    category: 'apis',
    tagline: 'Global PSTN calling, conference rooms, and TwiML WebSocket streaming.',
    description: 'Global PSTN calling, conference rooms, and TwiML WebSocket streaming.',
    voiceName: 'Hope',
    sampleText: "Twilio Voice Bridge established on port 8055. Streaming live call packets with sub-80ms jitter buffering.",
    sphereGradient: 'radial-gradient(circle at 35% 30%, #ecfdf5 0%, #10b981 35%, #059669 70%, #064e3b 100%)',
    avatarGradient: 'from-emerald-400 via-teal-500 to-cyan-700',
    glowColor: 'rgba(16, 185, 129, 0.45)',
    features: ['Global PSTN', 'TwiML Media Stream', 'Call Recording'],
  },
  {
    id: 'telephony-websocket',
    name: 'Full-Duplex WS',
    category: 'apis',
    tagline: 'Real-time WebSocket audio streaming with barge-in interruption and VAD.',
    description: 'Real-time WebSocket audio streaming with barge-in interruption and VAD.',
    voiceName: 'Aarohi',
    sampleText: "WebSocket audio channel open at ws://localhost:8000/ws/call. Live voice activity detection active.",
    sphereGradient: 'radial-gradient(circle at 35% 30%, #fff7ed 0%, #f97316 35%, #ea580c 65%, #7c2d12 100%)',
    avatarGradient: 'from-amber-400 via-orange-500 to-amber-700',
    glowColor: 'rgba(249, 115, 22, 0.45)',
    features: ['Full Duplex', 'Zero-Latency VAD', 'Barge-In Interrupt'],
  },
  {
    id: 'telephony-symphony-api',
    name: 'Summit REST WCF',
    category: 'apis',
    tagline: 'Automated Symphony Summit REST APIs for creating, updating, and querying IT incidents.',
    description: 'Automated Symphony Summit REST APIs for creating, updating, and querying IT incidents.',
    voiceName: 'Deepak',
    sampleText: "Executing Summit REST endpoint IM_GetIncidentDetails with proxy authentication and returning structured ticket JSON.",
    sphereGradient: 'radial-gradient(circle at 35% 30%, #eff6ff 0%, #3b82f6 35%, #1d4ed8 70%, #1e1b4b 100%)',
    avatarGradient: 'from-blue-400 via-indigo-500 to-cyan-600',
    glowColor: 'rgba(59, 130, 246, 0.45)',
    features: ['REST WCF Client', 'Proxy Auth Key', 'JSON Output'],
  },
  {
    id: 'telephony-telemetry',
    name: 'Carrier Telemetry',
    category: 'apis',
    tagline: 'Live CDR ingestion, sentiment analysis, call transcription, and SLA compliance metrics.',
    description: 'Live CDR ingestion, sentiment analysis, call transcription, and SLA compliance metrics.',
    voiceName: 'Radhika',
    sampleText: "All telemetry logs dispatched: 99.98% SLA compliance, average call handle time 2 minutes 14 seconds.",
    sphereGradient: 'radial-gradient(circle at 35% 30%, #fdf4ff 0%, #d946ef 35%, #8b5cf6 65%, #312e81 100%)',
    avatarGradient: 'from-purple-400 via-fuchsia-500 to-indigo-700',
    glowColor: 'rgba(217, 70, 239, 0.45)',
    features: ['Real-time CDR', 'Sentiment Scoring', '99.99% SLA Uptime'],
  },
];

export const VOICE_PERSONAS: VoicePersona[] = VOICE_STUDIO_PERSONAS;

export interface SpeechOptions {
  voice?: string;
  language?: string;
  rate?: number;
  pitch?: number;
  onEnd?: () => void;
  onError?: () => void;
}

const VOICE_PROFILES: Record<string, { gender: 'female' | 'male'; pitch: number; rate: number }> = {
  aarohi: { gender: 'female', pitch: 1.05, rate: 1.0 },
  radhika: { gender: 'female', pitch: 1.18, rate: 0.96 },
  hope: { gender: 'female', pitch: 1.25, rate: 1.05 },
  shweta: { gender: 'female', pitch: 1.1, rate: 1.0 },
  nalini: { gender: 'female', pitch: 1.0, rate: 0.95 },
  deepak: { gender: 'male', pitch: 0.78, rate: 0.95 },
  gaurav: { gender: 'male', pitch: 0.85, rate: 1.0 },
  alloy: { gender: 'male', pitch: 0.92, rate: 1.04 },
  hemraj: { gender: 'male', pitch: 0.72, rate: 0.9 },
};

export async function synthesizeSpeechLive(
  text: string,
  voiceOrOptions: string | SpeechOptions = 'Aarohi'
): Promise<HTMLAudioElement | null> {
  if (typeof window === 'undefined') return null;

  const options: SpeechOptions = typeof voiceOrOptions === 'string'
    ? { voice: voiceOrOptions }
    : voiceOrOptions;

  const voice = options.voice || 'Aarohi';
  const language = options.language || 'en-IN';
  const userRate = options.rate || 1.0;
  const userPitch = options.pitch || 1.0;

  const apiBase = process.env.NEXT_PUBLIC_BACKEND_API_URL || window.location.origin;

  try {
    const res = await fetch(`${apiBase}/api/tts/synthesize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        speaker: voice,
        voice: voice,
        language: language,
        language_code: language,
      }),
    });

    if (res.ok) {
      const blob = await res.blob();
      if (blob && blob.size > 100) {
        const audioUrl = URL.createObjectURL(blob);
        const audio = new Audio(audioUrl);
        return audio;
      }
    }
  } catch (e) {
    console.warn('Backend TTS failed, using browser SpeechSynthesis fallback', e);
  }

  // Fallback to Web Speech API with persona-specific voice and pitch mapping
  if ('speechSynthesis' in window) {
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);

    const key = voice.trim().toLowerCase();
    const profile = VOICE_PROFILES[key] || { gender: 'female', pitch: 1.0, rate: 1.0 };

    utterance.pitch = profile.pitch * userPitch;
    utterance.rate = profile.rate * userRate;

    const voices = window.speechSynthesis.getVoices();
    if (voices && voices.length > 0) {
      let matchedVoice: SpeechSynthesisVoice | undefined;

      if (profile.gender === 'male') {
        matchedVoice = voices.find(v =>
          /male|david|george|daniel|rishi|ravi|pradeep|mohan|james|guy/i.test(v.name) &&
          !/female|zira|susan|hazel|samantha|kavya/i.test(v.name)
        ) || voices.find(v => /male/i.test(v.name));
      } else {
        matchedVoice = voices.find(v =>
          /female|zira|susan|hazel|samantha|kavya|veena|priya|aditi/i.test(v.name)
        ) || voices.find(v => /female/i.test(v.name));
      }

      if (!matchedVoice && language) {
        const langShort = language.split('-')[0].toLowerCase();
        matchedVoice = voices.find(v => v.lang.toLowerCase().startsWith(langShort));
      }

      if (matchedVoice) {
        utterance.voice = matchedVoice;
      }
    }

    if (options.onEnd) {
      utterance.onend = () => options.onEnd?.();
    }
    if (options.onError) {
      utterance.onerror = () => options.onError?.();
    }

    window.speechSynthesis.speak(utterance);
  }
  return null;
}
