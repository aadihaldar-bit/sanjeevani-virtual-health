import { authFetch } from '@/lib/authFetch';
import { getApiBase } from '@/lib/api';

export type Role = 'super_admin' | 'tenant_admin' | 'manager' | 'operator' | 'viewer';
export type MemberRole = Exclude<Role, 'super_admin'>;

export interface Capabilities {
  manage_organizations: boolean;
  manage_members: boolean;
  manage_numbers: boolean;
  allocate_numbers: boolean;
  manage_external_models: boolean;
  manage_vector_stores: boolean;
  operate_calls: boolean;
  manage_agents_and_keys: boolean;
}

export interface Me {
  email: string;
  role: Role;
  tenant_id: number | null;
  tenant_name: string | null;
  tenant_status: string | null;
  is_super_admin: boolean;
  is_platform_staff: boolean;
  capabilities: Capabilities;
}

export interface Storage { status: 'ready' | 'failed' | 'creating' | 'disabled'; bucket?: string; error?: string | null; }
export interface Org { id: number; name: string; slug: string; status: string; plan: string | null; member_count?: number; is_system?: boolean; storage?: Storage | null; handover_number?: string | null; timezone?: string | null; calling_window?: { start: string; end: string } | null; }
export interface Member { id: number; tenant_id: number; email: string; role: Role; status: string; invited_by: string | null; }
export interface PhoneNumber {
  id: number; number: string; carrier: string; direction: string; tenant_id: number | null; workflow_id: number | null;
  label: string | null; status: 'unallocated' | 'allocated' | 'assigned';
}
export interface NumberEvent { id: number; action: string; from_tenant_id: number | null; to_tenant_id: number | null; actor: string | null; created_at: string | null; }
export interface AgentLite { id: number; name: string; tenant_id: number | null; }
export interface Credential { id: number; tenant_id: number; kind: string; provider: string; key_hint: string; updated_at: string | null; }
export interface ModelEndpoint {
  id: number; tenant_id: number | null; name: string; base_url: string; model: string; has_api_key: boolean; has_headers: boolean;
  supports_tools: boolean; enabled: boolean; last_test_ok: boolean | null; last_test_ms: number | null;
  last_test_error: string | null; last_tested_at: string | null; provider_id: string;
}
export interface VectorStore {
  id: number; tenant_id: number; provider: string; url: string; has_api_key: boolean; collection: string; mode: 'index' | 'read_only';
  text_field: string | null; source_field: string | null; dim: number | null; enabled: boolean;
  last_test_ok: boolean | null; last_test_ms: number | null; last_test_error: string | null; last_tested_at: string | null;
}
export interface AuditEntry { id: number; actor: string; tenant_id: number | null; action: string; target: string | null; detail: Record<string, unknown> | null; created_at: string | null; }

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

function messageFrom(body: any, fallback: string): string {
  const d = body?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((e: any) => e?.msg || '').filter(Boolean).join('; ') || fallback;
  return fallback;
}

/** JSON request to the backend; failures become an ApiError carrying the server's own explanation. */
export async function api<T = any>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const res = await authFetch(`${getApiBase()}${path}`, {
    method: init?.method || 'GET',
    headers: init?.body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  let data: any = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new ApiError(messageFrom(data, `Request failed (${res.status})`), res.status);
  return data as T;
}
