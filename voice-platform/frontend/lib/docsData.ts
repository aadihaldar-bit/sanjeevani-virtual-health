export interface ParameterDoc {
  name: string;
  type: 'string' | 'number' | 'boolean' | 'object' | 'array' | 'file';
  required: boolean;
  description: string;
  default?: string | number | boolean;
  enum?: string[];
  example?: any;
}

export interface HeaderDoc {
  name: string;
  type: string;
  required: boolean;
  description: string;
  example: string;
}

export interface ResponseDoc {
  status: number;
  description: string;
  example: any;
}

export interface EndpointDoc {
  id: string;
  category: string;
  categorySlug: string;
  title: string;
  method: 'GET' | 'POST' | 'WS' | 'PATCH' | 'DELETE';
  path: string;
  description: string;
  latencySla: string;
  productionReady: boolean;
  headers: HeaderDoc[];
  queryParams?: ParameterDoc[];
  bodyParams?: ParameterDoc[];
  defaultBody?: Record<string, any>;
  responses: ResponseDoc[];
  audioPreview?: {
    sampleVoice: string;
    sampleText: string;
    durationSec: number;
  };
}

export interface CategoryGroup {
  name: string;
  slug: string;
  description: string;
  icon: string;
  endpoints: EndpointDoc[];
}

// Bearer token header shared by dashboard endpoints (Firebase ID token; `demo_token` accepted in demo/sandbox).
const DASHBOARD_AUTH_HEADER: HeaderDoc = {
  name: 'Authorization',
  type: 'string',
  required: false,
  description: 'Bearer token for dashboard access (`Bearer <Firebase ID token>` or `Bearer demo_token` in demo environments).',
  example: 'Bearer demo_token',
};

// Bearer API-key header for the public /api/v1 integration endpoints (programmatic access).
const APIKEY_AUTH_HEADER: HeaderDoc = {
  name: 'Authorization',
  type: 'string',
  required: true,
  description: 'Bearer API key issued to your integration client. Required; requests without a valid active key return 401.',
  example: 'Bearer <your_api_key>',
};

const JSON_CONTENT_HEADER: HeaderDoc = {
  name: 'Content-Type',
  type: 'string',
  required: true,
  description: 'application/json',
  example: 'application/json',
};

export const API_CATEGORIES: CategoryGroup[] = [
  {
    name: 'Overview & Authentication',
    slug: 'overview',
    description: 'Core concepts and Bearer token authentication for the STL Digital Voice Agent. Dashboard endpoints accept a Firebase ID token (or `demo_token` in demo environments); public `/api/v1` integration endpoints require an issued API key.',
    icon: 'ShieldCheck',
    endpoints: [
      {
        id: 'auth-overview',
        category: 'Overview & Authentication',
        categorySlug: 'overview',
        title: 'Health & Availability',
        method: 'GET',
        path: '/health',
        description: 'Liveness probe. Verifies the service is up and the database connection is active, and reports the number of connected dashboard WebSocket clients. Public — no authentication required.',
        latencySla: '< 50ms',
        productionReady: true,
        headers: [],
        responses: [
          {
            status: 200,
            description: 'System healthy and database connection active.',
            example: {
              status: 'healthy',
              database: 'connected',
              dashboard_clients: 2,
              timestamp: '2026-10-05T16:22:26.007769',
            },
          },
        ],
      },
    ],
  },
  {
    name: 'Telephony & Call Control',
    slug: 'telephony',
    description: 'Outbound AI call placement over Tata Smartflo SIP trunking, click-to-call callbacks, and human-escalation tracking. Inbound PSTN calls are bridged to the real-time media pipeline internally (Asterisk/SIP → WebSocket media stream).',
    icon: 'PhoneCall',
    endpoints: [
      {
        id: 'call-outbound',
        category: 'Telephony & Call Control',
        categorySlug: 'telephony',
        title: 'Place Outbound Call (Dashboard)',
        method: 'POST',
        path: '/api/calls/outbound',
        description: 'Places an outbound voice call through Tata Smartflo, connecting the target number to the AI agent. Consumes the account call quota. Returns the carrier dispatch result.',
        latencySla: '~1–2s (carrier queue)',
        productionReady: true,
        headers: [DASHBOARD_AUTH_HEADER, JSON_CONTENT_HEADER],
        bodyParams: [
          {
            name: 'to_number',
            type: 'string',
            required: true,
            description: 'Target phone number in E.164 format.',
            example: '+919876543210',
          },
          {
            name: 'provider',
            type: 'string',
            required: false,
            description: 'Telephony provider override. Defaults to the configured carrier (Smartflo).',
            enum: ['smartflo'],
            example: 'smartflo',
          },
        ],
        defaultBody: {
          to_number: '+919876543210',
          provider: 'smartflo',
        },
        responses: [
          {
            status: 200,
            description: 'Call accepted and dispatched to the carrier.',
            example: {
              status: 'ok',
              provider: 'smartflo',
              data: { success: true, message: 'call initiated' },
              ref_id: 'STF-7f32a1',
              clean_e164: '+919876543210',
            },
          },
        ],
      },
      {
        id: 'call-outbound-v1',
        category: 'Telephony & Call Control',
        categorySlug: 'telephony',
        title: 'Initiate Outbound AI Call (Public API)',
        method: 'POST',
        path: '/api/v1/outbound/call',
        description: 'Programmatically queues an outbound AI call with a custom conversation context, optional persona/system prompt, and voice. Requires an API key. Creates a call record and returns a tracking call_id.',
        latencySla: '~1–2s (carrier queue)',
        productionReady: true,
        headers: [APIKEY_AUTH_HEADER, JSON_CONTENT_HEADER],
        bodyParams: [
          {
            name: 'phone_number',
            type: 'string',
            required: true,
            description: 'Target phone number with country code (E.164).',
            example: '+919876543210',
          },
          {
            name: 'message_context',
            type: 'string',
            required: true,
            description: 'Context the agent uses to drive the conversation.',
            example: 'Follow up on the printer setup support request raised yesterday.',
          },
          {
            name: 'recipient_name',
            type: 'string',
            required: false,
            description: 'Name of the person being called (used in the greeting).',
            example: 'Rahul',
          },
          {
            name: 'voice',
            type: 'string',
            required: false,
            description: 'Voice persona for the call.',
            default: 'Aarohi',
            example: 'Aarohi',
          },
          {
            name: 'provider',
            type: 'string',
            required: false,
            description: 'Telephony provider override.',
            enum: ['smartflo'],
            example: 'smartflo',
          },
        ],
        defaultBody: {
          phone_number: '+919876543210',
          message_context: 'Follow up on the printer setup support request raised yesterday.',
          recipient_name: 'Rahul',
          voice: 'Aarohi',
        },
        responses: [
          {
            status: 202,
            description: 'Outbound call queued.',
            example: {
              status: 'ok',
              call_id: '1423',
              message: 'Outbound call to Rahul successfully queued via smartflo',
              provider: 'smartflo',
              ref_id: 'STF-7f32a1',
            },
          },
        ],
      },
      {
        id: 'call-callback',
        category: 'Telephony & Call Control',
        categorySlug: 'telephony',
        title: 'Click-to-Call Callback',
        method: 'POST',
        path: '/api/calls/callback',
        description: 'Requests a callback — places an outbound Smartflo call to the given customer number and bridges it to the agent. Consumes the account call quota.',
        latencySla: '~1–2s (carrier queue)',
        productionReady: true,
        headers: [DASHBOARD_AUTH_HEADER, JSON_CONTENT_HEADER],
        bodyParams: [
          {
            name: 'customer_number',
            type: 'string',
            required: true,
            description: 'Customer phone number to call back (E.164).',
            example: '+919876543210',
          },
          {
            name: 'agent_number',
            type: 'string',
            required: false,
            description: 'Agent desk number reference.',
            example: '9000000002',
          },
        ],
        defaultBody: {
          customer_number: '+919876543210',
          agent_number: '9000000002',
        },
        responses: [
          {
            status: 200,
            description: 'Callback dispatched to the carrier.',
            example: {
              status: 'ok',
              provider: 'smartflo',
              data: { success: true, message: 'call initiated' },
              ref_id: 'STF-9a18c4',
              clean_e164: '+919876543210',
            },
          },
        ],
      },
      {
        id: 'escalations-list',
        category: 'Telephony & Call Control',
        categorySlug: 'telephony',
        title: 'List Human Escalations',
        method: 'GET',
        path: '/api/escalations',
        description: 'Returns call escalations to human agents with reason, status, and transfer metadata. Escalations are raised automatically by the agent when a caller requests a human or a conversation cannot be resolved autonomously.',
        latencySla: '< 100ms',
        productionReady: true,
        headers: [DASHBOARD_AUTH_HEADER],
        queryParams: [
          {
            name: 'status',
            type: 'string',
            required: false,
            description: 'Filter by escalation status.',
            enum: ['requested', 'started', 'completed', 'failed'],
            example: 'completed',
          },
          {
            name: 'limit',
            type: 'number',
            required: false,
            description: 'Max records to return (1–200).',
            default: 50,
            example: 50,
          },
          {
            name: 'offset',
            type: 'number',
            required: false,
            description: 'Pagination offset.',
            default: 0,
            example: 0,
          },
        ],
        responses: [
          {
            status: 200,
            description: 'List of escalation records.',
            example: {
              escalations: [
                {
                  id: 41,
                  call_id: 118,
                  reason: 'Caller requested a human agent for a password-expiry issue.',
                  status: 'completed',
                  requested_at: '2026-10-05T11:19:06Z',
                  started_at: '2026-10-05T11:19:20Z',
                  completed_at: '2026-10-05T11:24:02Z',
                  transferred_to: 'Service Desk',
                  notes: null,
                  caller_number: '+919000000002',
                  conversation_id: 'c-8941f2',
                },
              ],
              count: 1,
            },
          },
        ],
      },
      {
        id: 'escalations-metrics',
        category: 'Telephony & Call Control',
        categorySlug: 'telephony',
        title: 'Escalation Metrics',
        method: 'GET',
        path: '/api/escalations/metrics',
        description: 'Aggregated escalation statistics over a rolling window, including completion success rate.',
        latencySla: '< 120ms',
        productionReady: true,
        headers: [DASHBOARD_AUTH_HEADER],
        queryParams: [
          {
            name: 'days',
            type: 'number',
            required: false,
            description: 'Window size in days (1–365).',
            default: 30,
            example: 30,
          },
        ],
        responses: [
          {
            status: 200,
            description: 'Escalation metrics for the requested window.',
            example: {
              period_days: 30,
              total_escalations: 12,
              completed: 9,
              failed: 1,
              pending: 2,
              success_rate_percent: 75.0,
            },
          },
        ],
      },
    ],
  },
  {
    name: 'Neural Speech Engine',
    slug: 'speech',
    description: 'Indian-accented neural TTS and multi-lingual STT. Production stack: Deepgram nova-3 (speech-to-text), Cartesia Sonic (text-to-speech), with Groq Whisper / Sarvam Bulbul / Murf / Azure configured as fallbacks. Engine selection is config-driven via environment variables.',
    icon: 'Volume2',
    endpoints: [
      {
        id: 'speech-tts',
        category: 'Neural Speech Engine',
        categorySlug: 'speech',
        title: 'Synthesize Speech — Binary (Dashboard)',
        method: 'POST',
        path: '/api/tts/synthesize',
        description: 'Transforms text into natural Indian-accent speech using the configured TTS engine (Cartesia Sonic by default). Accepts JSON or form data. Returns a raw WAV audio stream in the response body (not JSON). Non-English Latin text is auto-translated before synthesis.',
        latencySla: '~450ms to first audio',
        productionReady: true,
        headers: [DASHBOARD_AUTH_HEADER, JSON_CONTENT_HEADER],
        bodyParams: [
          {
            name: 'text',
            type: 'string',
            required: true,
            description: 'Text string to synthesize.',
            example: 'Hello! I am your STL Digital AI voice assistant. How can I help you today?',
          },
          {
            name: 'voice',
            type: 'string',
            required: false,
            description: 'Target voice persona.',
            default: 'Aarohi',
            example: 'Aarohi',
          },
          {
            name: 'language_code',
            type: 'string',
            required: false,
            description: 'Language code tag.',
            enum: ['en-IN', 'hi-IN', 'mr-IN', 'ta-IN', 'te-IN'],
            default: 'en-IN',
            example: 'en-IN',
          },
        ],
        defaultBody: {
          text: 'Hello! I am your STL Digital AI voice assistant. How can I help you today?',
          voice: 'Aarohi',
          language_code: 'en-IN',
        },
        audioPreview: {
          sampleVoice: 'Aarohi (Female · en-IN)',
          sampleText: 'Hello! I am your STL Digital AI voice assistant. How can I help you today?',
          durationSec: 3.4,
        },
        responses: [
          {
            status: 200,
            description: 'Raw WAV audio stream returned directly in the response body.',
            example: {
              content_type: 'audio/wav',
              encoding: 'binary',
              note: 'Response body is raw WAV bytes (not JSON). Save it to a .wav file or pipe to an audio player.',
            },
          },
        ],
      },
      {
        id: 'speech-tts-v1',
        category: 'Neural Speech Engine',
        categorySlug: 'speech',
        title: 'Synthesize Speech — Base64 (Public API)',
        method: 'POST',
        path: '/api/v1/tts/synthesize',
        description: 'Programmatic TTS for integrations. Returns a Base64 data URL (MP3 or WAV) instead of a raw binary stream. Requires an API key.',
        latencySla: '~450ms',
        productionReady: true,
        headers: [APIKEY_AUTH_HEADER, JSON_CONTENT_HEADER],
        bodyParams: [
          {
            name: 'text',
            type: 'string',
            required: true,
            description: 'Text to synthesize (1–5000 characters).',
            example: 'Your incident ticket number 1268726 has been created.',
          },
          {
            name: 'voice',
            type: 'string',
            required: false,
            description: 'Voice persona.',
            default: 'Aarohi',
            example: 'Aarohi',
          },
          {
            name: 'format',
            type: 'string',
            required: false,
            description: 'Output audio format.',
            enum: ['mp3', 'wav'],
            default: 'mp3',
            example: 'mp3',
          },
        ],
        defaultBody: {
          text: 'Your incident ticket number 1268726 has been created.',
          voice: 'Aarohi',
          format: 'mp3',
        },
        responses: [
          {
            status: 200,
            description: 'Base64-encoded audio payload.',
            example: {
              audio_base64: 'data:audio/mp3;base64,SUQzBAAAAAAA...',
              format: 'mp3',
              voice: 'Aarohi',
              character_count: 54,
            },
          },
        ],
      },
      {
        id: 'speech-stt',
        category: 'Neural Speech Engine',
        categorySlug: 'speech',
        title: 'Speech-to-Text Transcription',
        method: 'POST',
        path: '/api/stt/transcribe',
        description: 'Transcribes an uploaded audio file using the configured STT engine (Deepgram nova-3 in production, with Groq Whisper / Sarvam / Cartesia fallbacks). Tuned for Indian-accented English.',
        latencySla: '~210ms',
        productionReady: true,
        headers: [
          DASHBOARD_AUTH_HEADER,
          {
            name: 'Content-Type',
            type: 'string',
            required: true,
            description: 'multipart/form-data',
            example: 'multipart/form-data',
          },
        ],
        bodyParams: [
          {
            name: 'audio_file',
            type: 'file',
            required: true,
            description: 'Audio file payload (WAV, MP3, OGG, or WebM format).',
          },
          {
            name: 'language_code',
            type: 'string',
            required: false,
            description: 'Expected primary language code.',
            enum: ['en-IN', 'hi-IN', 'mr-IN', 'ta-IN', 'te-IN'],
            default: 'en-IN',
            example: 'en-IN',
          },
        ],
        defaultBody: {
          language_code: 'en-IN',
        },
        responses: [
          {
            status: 200,
            description: 'Transcription result with language tag and latency.',
            example: {
              transcript: 'I need help resetting my network router password.',
              language_code: 'en-IN',
              latency_ms: 146,
            },
          },
        ],
      },
      {
        id: 'speech-credits',
        category: 'Neural Speech Engine',
        categorySlug: 'speech',
        title: 'Usage & Credits',
        method: 'GET',
        path: '/api/gnani/credits',
        description: 'Returns consumed vs. allotted speech usage for the account as a simple counter.',
        latencySla: '< 80ms',
        productionReady: true,
        headers: [DASHBOARD_AUTH_HEADER],
        responses: [
          {
            status: 200,
            description: 'Usage counter.',
            example: {
              used: 83,
              total: 1000,
            },
          },
        ],
      },
    ],
  },
  {
    name: 'Real-Time Streaming WebSocket',
    slug: 'streaming',
    description: 'Bi-directional WebSocket stream for live call events, transcripts, ticket updates, and dashboard KPIs — no HTTP polling.',
    icon: 'Radio',
    endpoints: [
      {
        id: 'ws-dashboard',
        category: 'Real-Time Streaming WebSocket',
        categorySlug: 'streaming',
        title: 'Dashboard Live Stream',
        method: 'WS',
        path: '/dashboard-stream',
        description: 'WebSocket connection that streams live operational events: new calls, call status changes, live transcripts, ticket creation/updates, knowledge-base changes, and agent handoffs. On connect it sends an `initial_state` snapshot. Send `{"type":"ping"}` to receive `{"type":"pong"}`.',
        latencySla: 'Real-time push',
        productionReady: true,
        headers: [
          {
            name: 'Upgrade',
            type: 'string',
            required: true,
            description: 'WebSocket protocol upgrade header.',
            example: 'websocket',
          },
        ],
        queryParams: [
          {
            name: 'token',
            type: 'string',
            required: false,
            description: 'Firebase ID token or `demo_token` passed as a query param for WebSocket authentication.',
            example: 'demo_token',
          },
        ],
        defaultBody: {
          type: 'ping',
        },
        responses: [
          {
            status: 101,
            description: 'Switching Protocols — initial_state snapshot sent on connect.',
            example: {
              type: 'initial_state',
              active_calls: [],
              recent_calls: [],
              stats: {
                total_calls: 86,
                active_calls: 0,
                escalated_calls: 4,
              },
              timestamp: '2026-10-05T16:22:26Z',
            },
          },
          {
            status: 200,
            description: 'Example broadcast event (ticket_created). Event types include: call_created, call_updated, call_completed, transcript, ticket_created, ticket_updated, agent_handoff, knowledge_updated.',
            example: {
              type: 'ticket_created',
              ticket: {
                ticket_id: '1268726',
                caller_number: '+919000000002',
                intent: 'Incident Management System',
                priority: 'P2-High',
                status: 'open',
              },
            },
          },
        ],
      },
    ],
  },
  {
    name: 'Knowledge Base & RAG',
    slug: 'knowledge',
    description: 'Enterprise document management for agent grounding. Uploaded manuals and policies are chunked and embedded into the vector store (Qdrant); the agent retrieves relevant context automatically during live calls.',
    icon: 'BookOpen',
    endpoints: [
      {
        id: 'knowledge-list',
        category: 'Knowledge Base & RAG',
        categorySlug: 'knowledge',
        title: 'List Knowledge Documents',
        method: 'GET',
        path: '/api/knowledge/documents',
        description: 'Returns all documents indexed in the knowledge base with chunk counts and sizes.',
        latencySla: '< 100ms',
        productionReady: true,
        headers: [DASHBOARD_AUTH_HEADER],
        responses: [
          {
            status: 200,
            description: 'Indexed documents.',
            example: {
              documents: [
                {
                  id: 'doc-1',
                  name: 'Brother_DCP_L2540DW_Manual.pdf',
                  size_kb: 1420.0,
                  chunks: 48,
                  uploaded_at: '2026-10-01T09:00:00Z',
                },
                {
                  id: 'doc-2',
                  name: 'Brother_India_Warranty_Terms_2025.pdf',
                  size_kb: 320.0,
                  chunks: 12,
                  uploaded_at: '2026-10-01T09:00:00Z',
                },
              ],
              total: 2,
            },
          },
        ],
      },
      {
        id: 'knowledge-read',
        category: 'Knowledge Base & RAG',
        categorySlug: 'knowledge',
        title: 'Read Knowledge Document',
        method: 'GET',
        path: '/api/knowledge/documents/{doc_id}',
        description: 'Returns the stored text of one document. Only a document of your own company is returned. The text of an uploaded document can be replaced with a PUT to the same path (body: `{ "content": "..." }`, administrators only); it is indexed again at once.',
        latencySla: '< 100ms',
        productionReady: true,
        headers: [DASHBOARD_AUTH_HEADER],
        responses: [
          {
            status: 200,
            description: 'The document.',
            example: {
              id: 'a1b2c3d4-...',
              name: 'Al Noor Clinic - Fees.md',
              title: 'Fees',
              content: '# Fees\n\n## Consultation\n\nThe team confirms the fee for each doctor.',
              editable: true,
            },
          },
          { status: 404, description: 'No such document for your company.', example: { detail: 'Document not found.' } },
        ],
      },
      {
        id: 'knowledge-upload',
        category: 'Knowledge Base & RAG',
        categorySlug: 'knowledge',
        title: 'Upload Knowledge Document',
        method: 'POST',
        path: '/api/knowledge/upload',
        description: 'Uploads a document, ingests it into the RAG vector store, and registers it in the knowledge base. Broadcasts a `knowledge_updated` event to the live stream.',
        latencySla: '~1–3s (ingest)',
        productionReady: true,
        headers: [
          DASHBOARD_AUTH_HEADER,
          {
            name: 'Content-Type',
            type: 'string',
            required: true,
            description: 'multipart/form-data',
            example: 'multipart/form-data',
          },
        ],
        bodyParams: [
          {
            name: 'file',
            type: 'file',
            required: true,
            description: 'Document to ingest (PDF, TXT, DOCX).',
          },
        ],
        responses: [
          {
            status: 200,
            description: 'Document ingested and indexed.',
            example: {
              status: 'success',
              document_id: 'a1b2c3d4-...',
              name: 'Brother_MFC_Maintenance_Manual.pdf',
            },
          },
        ],
      },
      {
        id: 'knowledge-delete',
        category: 'Knowledge Base & RAG',
        categorySlug: 'knowledge',
        title: 'Delete Knowledge Document',
        method: 'DELETE',
        path: '/api/knowledge/documents/{doc_id}',
        description: 'Removes a document from the knowledge base. Broadcasts a `knowledge_deleted` event.',
        latencySla: '< 100ms',
        productionReady: true,
        headers: [DASHBOARD_AUTH_HEADER],
        responses: [
          {
            status: 200,
            description: 'Document removed.',
            example: {
              status: 'deleted',
              document_id: 'a1b2c3d4-...',
            },
          },
        ],
      },
    ],
  },
  {
    name: 'Incident & Ticket Management',
    slug: 'tickets',
    description: 'Support ticket CRUD. During live calls the agent also raises incidents autonomously in Symphony SummitAI via internal MCP tools; these REST endpoints manage the local ticket records surfaced on the dashboard and to integrations.',
    icon: 'Ticket',
    endpoints: [
      {
        id: 'tickets-list',
        category: 'Incident & Ticket Management',
        categorySlug: 'tickets',
        title: 'List Support Tickets (Dashboard)',
        method: 'GET',
        path: '/api/tickets',
        description: 'Returns logged support tickets, newest first. Response is a JSON array of ticket objects.',
        latencySla: '< 100ms',
        productionReady: true,
        headers: [DASHBOARD_AUTH_HEADER],
        queryParams: [
          {
            name: 'limit',
            type: 'number',
            required: false,
            description: 'Max records to return (1–500).',
            default: 100,
            example: 100,
          },
          {
            name: 'offset',
            type: 'number',
            required: false,
            description: 'Pagination offset.',
            default: 0,
            example: 0,
          },
        ],
        responses: [
          {
            status: 200,
            description: 'Array of ticket objects (newest first).',
            example: [
              {
                id: 42,
                ticket_id: '1268726',
                caller_number: '+919000000002',
                intent: 'Incident Management System',
                issue: 'Password expired error on login.',
                priority: 'P2-High',
                transcript_summary: 'Symphony Summit Ticket: password expired error.',
                sentiment: 'Urgent',
                created_at: '2026-10-05T11:19:06Z',
              },
            ],
          },
        ],
      },
      {
        id: 'tickets-create',
        category: 'Incident & Ticket Management',
        categorySlug: 'tickets',
        title: 'Create Ticket (Dashboard)',
        method: 'POST',
        path: '/api/tickets/create',
        description: 'Creates a ticket record, links it to the related call, logs an event, and broadcasts `ticket_created` to the live stream.',
        latencySla: '< 150ms',
        productionReady: true,
        headers: [DASHBOARD_AUTH_HEADER, JSON_CONTENT_HEADER],
        bodyParams: [
          {
            name: 'caller_number',
            type: 'string',
            required: true,
            description: 'Caller phone number (E.164).',
            example: '+919000000002',
          },
          {
            name: 'intent',
            type: 'string',
            required: true,
            description: 'Classified call intent.',
            example: 'Incident Management System',
          },
          {
            name: 'issue',
            type: 'string',
            required: true,
            description: 'Issue description.',
            example: 'Password expired error on login.',
          },
          {
            name: 'priority',
            type: 'string',
            required: true,
            description: 'Ticket priority.',
            example: 'P2-High',
          },
          {
            name: 'transcript_summary',
            type: 'string',
            required: true,
            description: 'Short summary of the conversation.',
            example: 'Caller reported a password-expiry error at login.',
          },
        ],
        defaultBody: {
          caller_number: '+919000000002',
          intent: 'Incident Management System',
          issue: 'Password expired error on login.',
          priority: 'P2-High',
          transcript_summary: 'Caller reported a password-expiry error at login.',
        },
        responses: [
          {
            status: 200,
            description: 'Ticket created.',
            example: {
              ticket_id: 'TKT-4821',
              status: 'created',
              timestamp: '2026-10-05T11:19:06Z',
            },
          },
        ],
      },
      {
        id: 'tickets-create-v1',
        category: 'Incident & Ticket Management',
        categorySlug: 'tickets',
        title: 'Create Ticket (Public API)',
        method: 'POST',
        path: '/api/v1/tickets',
        description: 'Programmatically creates a ticket and fires a `ticket.created` webhook to subscribed endpoints. Requires an API key.',
        latencySla: '< 150ms',
        productionReady: true,
        headers: [APIKEY_AUTH_HEADER, JSON_CONTENT_HEADER],
        bodyParams: [
          {
            name: 'caller_number',
            type: 'string',
            required: true,
            description: 'Caller phone number (E.164).',
            example: '+919000000002',
          },
          {
            name: 'intent',
            type: 'string',
            required: true,
            description: 'Call intent.',
            example: 'Incident Management System',
          },
          {
            name: 'issue',
            type: 'string',
            required: true,
            description: 'Issue description.',
            example: 'Router reboot failed during firmware upgrade.',
          },
          {
            name: 'priority',
            type: 'string',
            required: false,
            description: 'Ticket priority.',
            default: 'medium',
            example: 'high',
          },
          {
            name: 'transcript_summary',
            type: 'string',
            required: false,
            description: 'Conversation summary.',
            example: 'Device shows solid amber light after OTA update.',
          },
        ],
        defaultBody: {
          caller_number: '+919000000002',
          intent: 'Incident Management System',
          issue: 'Router reboot failed during firmware upgrade.',
          priority: 'high',
          transcript_summary: 'Device shows solid amber light after OTA update.',
        },
        responses: [
          {
            status: 201,
            description: 'Ticket created.',
            example: {
              id: 43,
              ticket_id: 'TKT-9F2A4C1B',
              caller_number: '+919000000002',
              intent: 'Incident Management System',
              issue: 'Router reboot failed during firmware upgrade.',
              priority: 'high',
              transcript_summary: 'Device shows solid amber light after OTA update.',
              sentiment: null,
              created_at: '2026-10-05T11:30:00Z',
            },
          },
        ],
      },
      {
        id: 'tickets-get-v1',
        category: 'Incident & Ticket Management',
        categorySlug: 'tickets',
        title: 'Get Ticket by ID (Public API)',
        method: 'GET',
        path: '/api/v1/tickets/{ticket_id}',
        description: 'Retrieves a single ticket by its ticket_id. Requires an API key.',
        latencySla: '< 100ms',
        productionReady: true,
        headers: [APIKEY_AUTH_HEADER],
        responses: [
          {
            status: 200,
            description: 'Ticket record.',
            example: {
              id: 43,
              ticket_id: 'TKT-9F2A4C1B',
              caller_number: '+919000000002',
              intent: 'Incident Management System',
              issue: 'Router reboot failed during firmware upgrade.',
              priority: 'high',
              transcript_summary: 'Device shows solid amber light after OTA update.',
              sentiment: null,
              created_at: '2026-10-05T11:30:00Z',
            },
          },
          {
            status: 404,
            description: 'Ticket not found.',
            example: { detail: 'Ticket not found' },
          },
        ],
      },
      {
        id: 'tickets-update-v1',
        category: 'Incident & Ticket Management',
        categorySlug: 'tickets',
        title: 'Update Ticket (Public API)',
        method: 'PATCH',
        path: '/api/v1/tickets/{ticket_id}',
        description: 'Partially updates a ticket (priority, issue, summary, sentiment) and fires a `ticket.updated` webhook. Requires an API key.',
        latencySla: '< 150ms',
        productionReady: true,
        headers: [APIKEY_AUTH_HEADER, JSON_CONTENT_HEADER],
        bodyParams: [
          {
            name: 'priority',
            type: 'string',
            required: false,
            description: 'New priority.',
            example: 'critical',
          },
          {
            name: 'issue',
            type: 'string',
            required: false,
            description: 'Updated issue text.',
            example: 'Router fully bricked; replacement required.',
          },
          {
            name: 'transcript_summary',
            type: 'string',
            required: false,
            description: 'Updated summary.',
            example: 'Escalated to field replacement.',
          },
          {
            name: 'sentiment',
            type: 'string',
            required: false,
            description: 'Updated sentiment.',
            example: 'Frustrated',
          },
        ],
        defaultBody: {
          priority: 'critical',
          issue: 'Router fully bricked; replacement required.',
        },
        responses: [
          {
            status: 200,
            description: 'Ticket updated.',
            example: {
              id: 43,
              ticket_id: 'TKT-9F2A4C1B',
              caller_number: '+919000000002',
              intent: 'Incident Management System',
              issue: 'Router fully bricked; replacement required.',
              priority: 'critical',
              transcript_summary: 'Escalated to field replacement.',
              sentiment: 'Frustrated',
              created_at: '2026-10-05T11:30:00Z',
            },
          },
        ],
      },
    ],
  },
  {
    name: 'Platform Analytics & Telemetry',
    slug: 'analytics',
    description: 'Aggregated call volume, sentiment distribution, and intent breakdown computed from real call data.',
    icon: 'BarChart3',
    endpoints: [
      {
        id: 'analytics-trends',
        category: 'Platform Analytics & Telemetry',
        categorySlug: 'analytics',
        title: 'Trends — Volume, Sentiment, Intents',
        method: 'GET',
        path: '/api/analytics/trends',
        description: 'Returns daily call volume, sentiment distribution, and the top intents over a rolling window.',
        latencySla: '< 150ms',
        productionReady: true,
        headers: [DASHBOARD_AUTH_HEADER],
        queryParams: [
          {
            name: 'days',
            type: 'number',
            required: false,
            description: 'Window size in days (1–365).',
            default: 30,
            example: 30,
          },
        ],
        responses: [
          {
            status: 200,
            description: 'Trend time-series and breakdowns.',
            example: {
              volume: [
                { date: '10/03', calls: 14 },
                { date: '10/04', calls: 22 },
                { date: '10/05', calls: 19 },
              ],
              sentiment: [
                { name: 'Positive', value: 48 },
                { name: 'Neutral', value: 27 },
                { name: 'Urgent', value: 11 },
              ],
              intents: [
                { name: 'Incident Management System', count: 31 },
                { name: 'Password Reset', count: 18 },
                { name: 'Network & VPN Access', count: 9 },
              ],
            },
          },
        ],
      },
    ],
  },
];

export function generateCodeSnippet(
  endpoint: EndpointDoc,
  language: 'curl' | 'python' | 'typescript',
  host: string,
  token: string,
  bodyValues?: Record<string, any>
): string {
  const effectiveBody = bodyValues || endpoint.defaultBody || {};
  const isPostOrPatch = endpoint.method === 'POST' || endpoint.method === 'PATCH';
  const cleanHost = host.replace(/\/$/, '');
  const url = `${cleanHost}${endpoint.path}`;

  if (endpoint.method === 'WS') {
    const wsHost = cleanHost.replace(/^http/, 'ws');
    if (language === 'typescript') {
      return `// Real-Time WebSocket Client
const wsUrl = '${wsHost}${endpoint.path}?token=${token || 'demo_token'}';
const socket = new WebSocket(wsUrl);

socket.onopen = () => {
  console.log('Connected to STL Voice Agent stream');
  // Send heartbeat ping
  socket.send(JSON.stringify({ type: 'ping' }));
};

socket.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Received event:', data);
};

socket.onerror = (error) => {
  console.error('WebSocket error:', error);
};`;
    }
    if (language === 'python') {
      return `# Python websockets client
import asyncio
import json
import websockets

async def connect_stream():
    uri = "${wsHost}${endpoint.path}?token=${token || 'demo_token'}"
    async with websockets.connect(uri) as websocket:
        print("Connected to STL Voice Agent live stream")
        await websocket.send(json.dumps({"type": "ping"}))

        while True:
            response = await websocket.recv()
            print(f"Live event: {response}")

asyncio.run(connect_stream())`;
    }
    return `# Connect via websocat or wscat
wscat -c "${wsHost}${endpoint.path}?token=${token || 'demo_token'}"`;
  }

  if (language === 'curl') {
    let curl = `curl -X ${endpoint.method} "${url}" \\\n`;
    curl += `  -H "Authorization: Bearer ${token || 'demo_token'}" \\\n`;
    if (isPostOrPatch) {
      curl += `  -H "Content-Type: application/json" \\\n`;
      curl += `  -d '${JSON.stringify(effectiveBody, null, 2).replace(/\n/g, '\n  ')}'`;
    } else {
      curl = curl.slice(0, -3); // remove trailing slash
    }
    return curl;
  }

  if (language === 'python') {
    if (isPostOrPatch) {
      return `import requests

url = "${url}"
headers = {
    "Authorization": "Bearer ${token || 'demo_token'}",
    "Content-Type": "application/json"
}
payload = ${JSON.stringify(effectiveBody, null, 4)}

response = requests.${endpoint.method.toLowerCase()}(url, json=payload, headers=headers)
print(response.status_code)
print(response.json())`;
    } else {
      return `import requests

url = "${url}"
headers = {
    "Authorization": "Bearer ${token || 'demo_token'}"
}

response = requests.get(url, headers=headers)
print(response.status_code)
print(response.json())`;
    }
  }

  if (language === 'typescript') {
    if (isPostOrPatch) {
      return `// TypeScript fetch client
async function executeRequest() {
  const res = await fetch('${url}', {
    method: '${endpoint.method}',
    headers: {
      'Authorization': 'Bearer ${token || 'demo_token'}',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(${JSON.stringify(effectiveBody, null, 4)}),
  });

  const data = await res.json();
  console.log('Status:', res.status);
  console.log('Data:', data);
}

executeRequest();`;
    } else {
      return `// TypeScript fetch client
async function executeRequest() {
  const res = await fetch('${url}', {
    method: 'GET',
    headers: {
      'Authorization': 'Bearer ${token || 'demo_token'}',
    },
  });

  const data = await res.json();
  console.log('Status:', res.status);
  console.log('Data:', data);
}

executeRequest();`;
    }
  }

  return '';
}
