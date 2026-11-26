/**
 * REST API Client
 * HTTP client for fetching data from the dashboard backend.
 */

import { getViewTenant } from './tenantView';

export function getApiBase(): string {
  if (typeof window !== 'undefined') {
    const envUrl = process.env.NEXT_PUBLIC_BACKEND_API_URL;
    if (envUrl && window.location.protocol === 'https:') {
      return envUrl.replace(/^http:/, 'https:');
    }
    if (envUrl) return envUrl;
    const host = window.location.hostname;
    const port = window.location.port;
    if ((host === 'localhost' || host === '127.0.0.1') && port !== '8000') {
      return `http://${host}:8055`;
    }
    return window.location.origin;
  }
  return process.env.NEXT_PUBLIC_BACKEND_API_URL || 'http://127.0.0.1:8055';
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const apiBase = getApiBase();
  const { getIdToken } = await import('./useAuth');
  const token = await getIdToken();

  const headers = new Headers(options?.headers);
  if (!headers.has('Content-Type') && !(options?.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  const viewing = getViewTenant();            // a system admin looking at one company
  if (viewing) headers.set('X-Tenant-Id', viewing);

  const res = await fetch(`${apiBase}${path}`, {
    ...options,
    headers,
  });
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${await res.text()}`);
  }
  return res.json();
}

// ─── Calls ─────────────────────────────────────────────────────

export interface CallData {
  id: number;
  conversation_id: string | null;
  call_sid: string | null;
  caller_number: string;
  called_number: string;
  agent_id: string | null;
  status: 'active' | 'completed' | 'escalated' | 'failed';
  live_status: 'initiated' | 'ringing' | 'answered' | 'in-progress' | 'in_progress' | 'started' | 'active' | 'completed' | 'failed' | 'busy' | 'no-answer' | 'canceled' | null;
  source: 'twilio' | 'elevenlabs' | 'simulator' | string | null;
  started_at: string;
  ended_at: string | null;
  last_event_at: string | null;
  duration_seconds: number | null;
  end_reason: string | null;
  is_escalated: boolean;
  transferred_to_human: boolean;
  current_agent: string | null;
  sentiment: string | null;
  intent: string | null;
  summary: string | null;
  latest_ticket: TicketData | null;
  latest_appointment?: AppointmentData | null;
  security?: SecurityData | null;
  kyc?: KycSummary | null;
}

export interface GuardEvent {
  turn: number;
  is_jailbreak: boolean;
  jailbreak_probability: number;
  attack_type: string;
  attack_type_confidence: number;
  threat_severity: number;
  guard_engine: string;
  source?: string;
}

export interface SecurityData {
  guard_events: GuardEvent[];
  max_threat_severity: number;
  jailbreak_attempts: number;
  attack_types_seen: string[];
  turns_checked?: number;
}

export interface SecuritySummary {
  days: number;
  calls_total: number;
  calls_checked: number;
  turns_checked: number;
  flagged_calls: number;
  jailbreak_attempts: number;
  severity_counts: Record<string, number>;
  attack_types: Record<string, number>;
  recent_flagged: {
    call_id: number;
    caller_number: string | null;
    started_at: string | null;
    max_threat_severity: number;
    attack_types_seen: string[];
  }[];
}

/** Patient identity (KYC) verification summary — no Aadhaar number, no photos. */
export interface KycSummary {
  id: number;
  call_id: number | null;
  status: 'pending' | 'consented' | 'qr_verified' | 'verified' | 'failed' | 'expired';
  trust: 'uidai' | 'demo' | 'document' | null;
  verified_name: string | null;
  birth_year: string | null;
  aadhaar_last4: string | null;
  face_score: number | null;
  liveness_passed: boolean | null;
  name_match: number | null;
  failure_reason: string | null;
  demo: boolean;
  verified_at: string | null;
  created_at: string | null;
}

export interface AppointmentData {
  id: number;
  appointment_id: string;
  call_id: number | null;
  patient_name: string | null;
  caller_number: string | null;
  is_new_patient: boolean;
  problem: string | null;
  doctor_name: string | null;
  doctor_specialty: string | null;
  appointment_time: string | null;
  status: string | null;
  notes: string | null;
  created_at: string | null;
}

export interface TranscriptMessage {
  id: number;
  role: 'user' | 'agent';
  message: string;
  timestamp: string;
  sequence_number: number;
}

export interface CallDetail extends CallData {
  transcript: TranscriptMessage[];
  escalations: EscalationData[];
  appointments?: AppointmentData[];
}

export interface EscalationData {
  id: number;
  call_id: number;
  reason: string | null;
  status: 'requested' | 'started' | 'completed' | 'failed';
  requested_at: string;
  started_at: string | null;
  completed_at: string | null;
  transferred_to: string | null;
  notes: string | null;
  caller_number?: string;
  conversation_id?: string;
}

export interface AnalyticsSummary {
  period_days: number;
  total_calls: number;
  active_calls: number;
  completed_calls: number;
  ai_resolved_calls: number;
  escalated_calls: number;
  transferred_calls: number;
  transfer_rate_percent: number;
  average_duration_seconds: number;
}

/** What the Home page shows as key figures, for one stretch of time (see /api/analytics/kpis). */
export interface KpiFigures {
  conversations: number;
  resolved: number;
  resolved_rate_percent: number | null;
  handed_over: number;
  average_duration_seconds: number | null;
  talk_seconds: number;
  tickets: number;
  appointments: number;
  positive: number;
  neutral: number;
  negative: number;
  test_calls: number;
}

export interface Kpis {
  period_days: number;
  current: KpiFigures & { rated: number };
  /** the same figures for the stretch of equal length just before */
  previous: KpiFigures;
  calls_today: number;
  /** the company's time zone, which the hour below is read in */
  timezone?: string;
  /** hour of the day (0-23) with the most calls in that zone, null when there are no calls */
  peak_hour?: number | null;
  peak_hour_ist: number | null;
  peak_hour_calls: number;
  top_agent: { name: string; calls: number } | null;
}

export interface DailyData {
  date: string;
  total_calls: number;
  escalated_calls: number;
  ai_resolved: number;
}

export interface TicketData {
  id?: number;
  ticket_id: string;
  call_id: number | null;
  caller_number: string;
  intent: string;
  issue: string;
  priority: string;
  transcript_summary: string;
  sentiment: string | null;
  created_at: string;
}

export interface EventData {
  id: number;
  call_id: number | null;
  event_type: string;
  payload: Record<string, unknown> | null;
  created_at: string;
}

export interface DiagnosticStages {
  live_received: boolean;
  transcript_saved: boolean;
  ai_summary_created: boolean;
  ticket_created: boolean;
  escalation_detected: boolean;
  issues: string[];
}

export interface DiagnosticCall {
  call: CallData;
  transcript_count: number;
  event_count: number;
  events: EventData[];
  stages: DiagnosticStages;
}

export interface DiagnosticsOverview {
  summary: {
    checked_calls: number;
    calls_with_issues: number;
    open_issues: number;
    recent_event_count: number;
  };
  recent_events: EventData[];
  calls: DiagnosticCall[];
}

// ─── API Functions ─────────────────────────────────────────────

export async function fetchCalls(
  status?: string,
  limit = 50,
  offset = 0
): Promise<{ calls: CallData[]; total: number }> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (status) params.set('status', status);
  return apiFetch(`/api/calls?${params}`);
}

export async function fetchActiveCalls(): Promise<{ calls: CallData[]; count: number }> {
  return apiFetch('/api/calls/active');
}

export async function fetchCallDetail(callId: number): Promise<CallDetail> {
  return apiFetch(`/api/calls/${callId}`);
}

export async function fetchHealthCalls(
  limit = 50,
  offset = 0
): Promise<{ calls: CallData[]; total: number }> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return apiFetch(`/api/health/calls?${params}`);
}

export async function fetchHealthCallDetail(callId: number): Promise<CallDetail> {
  return apiFetch(`/api/health/calls/${callId}`);
}

export async function fetchTranscript(
  callId: number
): Promise<{ messages: TranscriptMessage[]; count: number }> {
  return apiFetch(`/api/calls/${callId}/transcript`);
}

export async function fetchAnalyticsSummary(days = 30): Promise<AnalyticsSummary> {
  return apiFetch(`/api/analytics/summary?days=${days}`);
}

export async function fetchKpis(days = 30): Promise<Kpis> {
  return apiFetch(`/api/analytics/kpis?days=${days}`);
}

export async function fetchSecuritySummary(days = 30): Promise<SecuritySummary> {
  return apiFetch(`/api/security/summary?days=${days}`);
}

export async function fetchDailyAnalytics(
  days = 14
): Promise<{ data: DailyData[] }> {
  return apiFetch(`/api/analytics/daily?days=${days}`);
}

export async function fetchAnalyticsTrends(days = 30): Promise<{
  volume: { date: string; calls: number }[];
  sentiment: { name: string; value: number }[];
  intents: { name: string; count: number }[];
}> {
  return apiFetch(`/api/analytics/trends?days=${days}`);
}

export async function uploadKnowledgeDocument(file: File): Promise<{ status: string; document_id: string; chunks: number; semantic: boolean }> {
  const formData = new FormData();
  formData.append('file', file);
  return apiFetch('/api/knowledge/upload', {
    method: 'POST',
    headers: {}, // Do not set Content-Type, fetch sets it automatically with boundary for FormData
    body: formData,
  });
}

export async function fetchEscalations(
  status?: string,
  limit = 50
): Promise<{ escalations: EscalationData[]; count: number }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (status) params.set('status', status);
  return apiFetch(`/api/escalations?${params}`);
}

export async function fetchEscalationMetrics(
  days = 30
): Promise<{
  total_escalations: number;
  completed: number;
  failed: number;
  pending: number;
  success_rate_percent: number;
}> {
  return apiFetch(`/api/escalations/metrics?days=${days}`);
}


export async function initiateOutboundCall(toNumber: string): Promise<{ status: string; call_sid: string }> {
  return apiFetch(`/api/calls/outbound`, {
    method: 'POST',
    body: JSON.stringify({ to_number: toNumber }),
  });
}


export interface CallAnalysis {
  sentiment: string;
  sentiment_score: number;
  customer_emotion: string;
  escalation_risk: string;
  key_issues: string[];
  summary: string;
  confidence: number;
}

export async function analyzeTranscript(callId: number): Promise<CallAnalysis> {
  return apiFetch(`/api/calls/${callId}/analyze`, { method: 'POST' });
}

export async function fetchDiagnosticsOverview(limit = 15): Promise<DiagnosticsOverview> {
  return apiFetch(`/api/diagnostics/overview?limit=${limit}`);
}
