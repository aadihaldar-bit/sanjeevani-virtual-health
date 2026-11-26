'use client';

import React, { useState, useEffect, useCallback } from 'react';
import {
  KeyRound, Copy, Eye, EyeOff, RefreshCw, Trash2, Plus, CheckCircle2,
  AlertCircle, Webhook, Clock, ChevronDown, ChevronUp,
  ExternalLink, Info,
} from 'lucide-react';
import { getApiBase } from '@/lib/api';

const API_BASE = getApiBase();

// ─── Types ─────────────────────────────────────────────────────────────────

interface APIClient {
  client_id: number;
  name: string;
  company: string | null;
  email: string | null;
  plan: string;
  key_prefix: string;
  is_active: boolean;
  rate_limit_per_minute: number;
  daily_call_quota: number;
  monthly_call_quota: number;
  calls_today: number;
  calls_this_month: number;
  daily_remaining: number;
  monthly_remaining: number;
  created_at: string | null;
}

interface RegisterResponse {
  api_key: string;
  key_prefix: string;
  client_id: number;
  email: string;
  plan: string;
  daily_quota: number;
  monthly_quota: number;
  message: string;
}

interface WebhookEndpoint {
  id: number;
  url: string;
  events: string[];
  is_active: boolean;
  created_at: string;
}

const AVAILABLE_EVENTS = [
  'call.started', 'call.ended', 'call.transcribed',
  'ticket.created', 'ticket.updated', 'chat.turn',
];

// ─── Helpers ────────────────────────────────────────────────────────────────

function copyToClipboard(text: string) {
  navigator.clipboard.writeText(text).catch(() => {});
}

function Badge({ plan }: { plan: string }) {
  const colors: Record<string, string> = {
    free: 'bg-stone-100 text-stone-600 dark:bg-white/10 dark:text-stone-300',
    pro: 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
    enterprise: 'bg-violet-50 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300',
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider ${colors[plan] || colors.free}`}>
      {plan}
    </span>
  );
}

function QuotaBar({ used, total, label }: { used: number; total: number; label: string }) {
  const pct = total > 0 ? Math.min(100, (used / total) * 100) : 0;
  const color = pct > 85 ? 'bg-red-500' : pct > 60 ? 'bg-amber-500' : 'bg-emerald-500';
  return (
    <div>
      <div className="flex justify-between text-[10px] text-stone-500 dark:text-stone-400 mb-1">
        <span>{label}</span>
        <span>{used} / {total}</span>
      </div>
      <div className="h-1.5 rounded-full bg-stone-200 dark:bg-white/10 overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

// ─── Register Form ──────────────────────────────────────────────────────────

function RegisterForm({ onRegistered }: { onRegistered: (key: string, client: RegisterResponse) => void }) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [company, setCompany] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, email, company: company || undefined }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Registration failed');
      onRegistered(data.api_key, data);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium text-stone-600 dark:text-stone-400 mb-1">Your Name / Service Name *</label>
          <input
            required value={name} onChange={e => setName(e.target.value)}
            placeholder="GCP Monitoring Bot"
            className="w-full px-3 py-2 text-sm rounded-lg border border-stone-200 dark:border-white/10 bg-white dark:bg-white/5 text-stone-900 dark:text-white placeholder-stone-400 focus:outline-none focus:ring-2 focus:ring-stone-400"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-stone-600 dark:text-stone-400 mb-1">STL Email *</label>
          <input
            required type="email" value={email} onChange={e => setEmail(e.target.value)}
            placeholder="you@stltech.in"
            className="w-full px-3 py-2 text-sm rounded-lg border border-stone-200 dark:border-white/10 bg-white dark:bg-white/5 text-stone-900 dark:text-white placeholder-stone-400 focus:outline-none focus:ring-2 focus:ring-stone-400"
          />
        </div>
      </div>
      <div>
        <label className="block text-xs font-medium text-stone-600 dark:text-stone-400 mb-1">Team / Department (optional)</label>
        <input
          value={company} onChange={e => setCompany(e.target.value)}
          placeholder="Infrastructure Team"
          className="w-full px-3 py-2 text-sm rounded-lg border border-stone-200 dark:border-white/10 bg-white dark:bg-white/5 text-stone-900 dark:text-white placeholder-stone-400 focus:outline-none focus:ring-2 focus:ring-stone-400"
        />
      </div>
      {error && (
        <div className="flex items-center gap-2 text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 px-3 py-2 rounded-lg">
          <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" /> {error}
        </div>
      )}
      <div className="flex items-center gap-2 text-[11px] text-stone-500 dark:text-stone-400 bg-stone-50 dark:bg-white/5 px-3 py-2 rounded-lg">
        <Info className="w-3.5 h-3.5 flex-shrink-0" />
        Only <strong>stl.tech, stltech.in, stldigital.com, stldigitaltech.com</strong> email domains are allowed.
      </div>
      <button
        type="submit" disabled={loading}
        className="w-full py-2 px-4 rounded-lg bg-stone-900 dark:bg-white text-white dark:text-stone-900 text-sm font-medium hover:bg-stone-700 dark:hover:bg-stone-100 transition-colors disabled:opacity-50"
      >
        {loading ? 'Generating...' : 'Generate API Key'}
      </button>
    </form>
  );
}

// ─── New Key Banner ──────────────────────────────────────────────────────────

function NewKeyBanner({ apiKey, onDismiss }: { apiKey: string; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false);
  const [shown, setShown] = useState(false);

  function handleCopy() {
    copyToClipboard(apiKey);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="rounded-xl border border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/20 p-4 space-y-3">
      <div className="flex items-center gap-2">
        <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
        <span className="text-sm font-semibold text-emerald-800 dark:text-emerald-300">API Key Generated!</span>
      </div>
      <p className="text-xs text-emerald-700 dark:text-emerald-400">
        ⚠️ This is the <strong>only time</strong> this key will be shown. Copy it now and store it securely.
      </p>
      <div className="flex items-center gap-2">
        <code className="flex-1 px-3 py-2 rounded-lg bg-white dark:bg-black/40 text-xs font-mono text-stone-800 dark:text-stone-200 border border-emerald-200 dark:border-emerald-800 overflow-x-auto select-all">
          {shown ? apiKey : '•'.repeat(apiKey.length)}
        </code>
        <button onClick={() => setShown(s => !s)} className="p-2 rounded-lg hover:bg-emerald-100 dark:hover:bg-emerald-800/30 text-emerald-700 dark:text-emerald-400">
          {shown ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
        </button>
        <button onClick={handleCopy} className="p-2 rounded-lg hover:bg-emerald-100 dark:hover:bg-emerald-800/30 text-emerald-700 dark:text-emerald-400">
          {copied ? <CheckCircle2 className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
        </button>
      </div>
      <div className="text-[11px] text-emerald-700 dark:text-emerald-400 font-mono">
        Usage: <code className="bg-white/60 dark:bg-black/30 px-1.5 py-0.5 rounded">Authorization: Bearer {apiKey.slice(0, 20)}...</code>
      </div>
      <button onClick={onDismiss} className="text-xs text-emerald-600 dark:text-emerald-400 underline">
        I've copied it — dismiss
      </button>
    </div>
  );
}

// ─── My Profile Card ─────────────────────────────────────────────────────────

function MyProfileCard({ apiKey, onRotated }: { apiKey: string; onRotated: (newKey: string) => void }) {
  const [profile, setProfile] = useState<APIClient | null>(null);
  const [loading, setLoading] = useState(true);
  const [rotating, setRotating] = useState(false);
  const [error, setError] = useState('');

  const fetchProfile = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/me`, {
        headers: { Authorization: `Bearer ${apiKey}` },
      });
      if (!res.ok) throw new Error('Failed to load profile');
      setProfile(await res.json());
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [apiKey]);

  useEffect(() => { fetchProfile(); }, [fetchProfile]);

  async function handleRotate() {
    if (!confirm('Rotate your API key? The current key will be immediately invalidated.')) return;
    setRotating(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/me/rotate-key`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${apiKey}` },
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Rotation failed');
      onRotated(data.api_key);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setRotating(false);
    }
  }

  if (loading) return (
    <div className="flex items-center gap-2 py-4 text-xs text-stone-400">
      <div className="w-4 h-4 border-2 border-stone-200 dark:border-white/10 border-t-stone-500 rounded-full animate-spin" /> Loading profile...
    </div>
  );

  if (error) return <div className="text-xs text-red-500">{error}</div>;
  if (!profile) return null;

  return (
    <div className="rounded-xl border border-stone-200 dark:border-white/10 bg-white dark:bg-white/5 p-4 space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-stone-900 dark:text-white">{profile.name}</h3>
            <Badge plan={profile.plan} />
            {profile.is_active
              ? <span className="flex items-center gap-1 text-[10px] text-emerald-600 dark:text-emerald-400"><CheckCircle2 className="w-3 h-3" /> Active</span>
              : <span className="text-[10px] text-red-500">Inactive</span>}
          </div>
          <p className="text-xs text-stone-500 dark:text-stone-400 mt-0.5">{profile.email}</p>
          {profile.company && <p className="text-[11px] text-stone-400 dark:text-stone-500">{profile.company}</p>}
        </div>
        <div className="flex items-center gap-1">
          <code className="text-[10px] font-mono text-stone-500 dark:text-stone-400 bg-stone-100 dark:bg-white/10 px-2 py-1 rounded">
            {profile.key_prefix}...
          </code>
          <button
            onClick={handleRotate} disabled={rotating}
            title="Rotate API key"
            className="p-1.5 rounded-lg hover:bg-stone-100 dark:hover:bg-white/10 text-stone-500 dark:text-stone-400 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${rotating ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Quotas */}
      <div className="space-y-2.5">
        <QuotaBar used={profile.calls_today} total={profile.daily_call_quota} label="Today's calls" />
        <QuotaBar used={profile.calls_this_month} total={profile.monthly_call_quota} label="This month's calls" />
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-2 pt-1">
        {[
          { label: 'Daily remaining', value: profile.daily_remaining },
          { label: 'Monthly remaining', value: profile.monthly_remaining },
          { label: 'Rate limit / min', value: profile.rate_limit_per_minute },
        ].map(({ label, value }) => (
          <div key={label} className="bg-stone-50 dark:bg-white/5 rounded-lg px-3 py-2 text-center">
            <div className="text-sm font-semibold text-stone-900 dark:text-white">{value}</div>
            <div className="text-[10px] text-stone-500 dark:text-stone-400 leading-tight mt-0.5">{label}</div>
          </div>
        ))}
      </div>

      {profile.created_at && (
        <p className="text-[10px] text-stone-400 flex items-center gap-1">
          <Clock className="w-3 h-3" /> Registered {new Date(profile.created_at).toLocaleDateString()}
        </p>
      )}
    </div>
  );
}

// ─── Webhook Manager ─────────────────────────────────────────────────────────

function WebhookManager({ apiKey }: { apiKey: string }) {
  const [webhooks, setWebhooks] = useState<WebhookEndpoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [url, setUrl] = useState('');
  const [secret, setSecret] = useState('');
  const [selectedEvents, setSelectedEvents] = useState<string[]>(['call.ended']);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const loadWebhooks = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/webhooks`, {
        headers: { Authorization: `Bearer ${apiKey}` },
      });
      if (res.ok) setWebhooks(await res.json());
    } finally {
      setLoading(false);
    }
  }, [apiKey]);

  useEffect(() => { loadWebhooks(); }, [loadWebhooks]);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setSaving(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/webhooks`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, events: selectedEvents, secret: secret || undefined }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to add webhook');
      setWebhooks(w => [data, ...w]);
      setShowForm(false);
      setUrl(''); setSecret('');
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number) {
    if (!confirm('Delete this webhook?')) return;
    await fetch(`${API_BASE}/api/v1/webhooks/${id}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${apiKey}` },
    });
    setWebhooks(w => w.filter(e => e.id !== id));
  }

  return (
    <div className="rounded-xl border border-stone-200 dark:border-white/10 bg-white dark:bg-white/5 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-stone-100 dark:border-white/10">
        <div className="flex items-center gap-2">
          <Webhook className="w-4 h-4 text-stone-500" />
          <span className="text-sm font-semibold text-stone-900 dark:text-white">Webhooks</span>
          <span className="text-[10px] text-stone-400 bg-stone-100 dark:bg-white/10 px-1.5 py-0.5 rounded-full">{webhooks.length}</span>
        </div>
        <button
          onClick={() => setShowForm(s => !s)}
          className="flex items-center gap-1.5 text-xs text-stone-600 dark:text-stone-400 hover:text-stone-900 dark:hover:text-white px-2 py-1 rounded-lg hover:bg-stone-100 dark:hover:bg-white/10 transition-colors"
        >
          <Plus className="w-3.5 h-3.5" /> Add endpoint
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleAdd} className="px-4 py-3 bg-stone-50 dark:bg-white/5 border-b border-stone-100 dark:border-white/10 space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <div className="col-span-2">
              <label className="block text-[10px] font-medium text-stone-500 mb-1">HTTPS URL *</label>
              <input required value={url} onChange={e => setUrl(e.target.value)}
                placeholder="https://yourapp.example.com/webhooks/stl"
                className="w-full px-3 py-1.5 text-xs rounded-lg border border-stone-200 dark:border-white/10 bg-white dark:bg-black/20 text-stone-900 dark:text-white placeholder-stone-400 focus:outline-none focus:ring-1 focus:ring-stone-400"
              />
            </div>
            <div>
              <label className="block text-[10px] font-medium text-stone-500 mb-1">Signing Secret (optional)</label>
              <input value={secret} onChange={e => setSecret(e.target.value)}
                placeholder="mysecret"
                className="w-full px-3 py-1.5 text-xs rounded-lg border border-stone-200 dark:border-white/10 bg-white dark:bg-black/20 text-stone-900 dark:text-white placeholder-stone-400 focus:outline-none focus:ring-1 focus:ring-stone-400"
              />
            </div>
          </div>
          <div>
            <label className="block text-[10px] font-medium text-stone-500 mb-1">Events *</label>
            <div className="flex flex-wrap gap-1.5">
              {AVAILABLE_EVENTS.map(ev => (
                <button key={ev} type="button"
                  onClick={() => setSelectedEvents(s => s.includes(ev) ? s.filter(x => x !== ev) : [...s, ev])}
                  className={`px-2 py-0.5 rounded-full text-[10px] font-medium transition-colors ${
                    selectedEvents.includes(ev)
                      ? 'bg-stone-900 text-white dark:bg-white dark:text-stone-900'
                      : 'bg-stone-100 dark:bg-white/10 text-stone-600 dark:text-stone-400 hover:bg-stone-200 dark:hover:bg-white/20'
                  }`}
                >{ev}</button>
              ))}
            </div>
          </div>
          {error && <p className="text-xs text-red-500">{error}</p>}
          <div className="flex gap-2">
            <button type="submit" disabled={saving || selectedEvents.length === 0}
              className="px-3 py-1.5 rounded-lg bg-stone-900 dark:bg-white text-white dark:text-stone-900 text-xs font-medium disabled:opacity-50">
              {saving ? 'Saving...' : 'Save'}
            </button>
            <button type="button" onClick={() => setShowForm(false)}
              className="px-3 py-1.5 rounded-lg text-xs text-stone-500 hover:bg-stone-100 dark:hover:bg-white/10">Cancel</button>
          </div>
        </form>
      )}

      <div className="divide-y divide-stone-100 dark:divide-white/5">
        {loading ? (
          <div className="px-4 py-3 text-xs text-stone-400">Loading...</div>
        ) : webhooks.length === 0 ? (
          <div className="px-4 py-6 text-center text-xs text-stone-400">No webhooks registered. Add one to receive events.</div>
        ) : webhooks.map(ep => (
          <div key={ep.id} className="px-4 py-3 flex items-start gap-3">
            <div className={`w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0 ${ep.is_active ? 'bg-emerald-500' : 'bg-stone-400'}`} />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-mono text-stone-700 dark:text-stone-300 truncate">{ep.url}</p>
              <div className="flex flex-wrap gap-1 mt-1">
                {ep.events.map(ev => (
                  <span key={ev} className="px-1.5 py-0.5 rounded-full bg-stone-100 dark:bg-white/10 text-[10px] text-stone-500 dark:text-stone-400">{ev}</span>
                ))}
              </div>
            </div>
            <button onClick={() => handleDelete(ep.id)}
              className="p-1 rounded hover:bg-red-50 dark:hover:bg-red-900/20 text-stone-400 hover:text-red-500 transition-colors">
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Endpoint Reference ──────────────────────────────────────────────────────

const ENDPOINT_GROUPS = [
  {
    group: 'Auth', color: 'text-violet-600 dark:text-violet-400',
    endpoints: [
      { method: 'POST', path: '/api/v1/auth/register', desc: 'Register & get API key (STL email required, no token needed)' },
      { method: 'GET',  path: '/api/v1/me', desc: 'Get your profile + quota' },
      { method: 'POST', path: '/api/v1/me/rotate-key', desc: 'Rotate your API key' },
    ],
  },
  {
    group: 'Chat', color: 'text-blue-600 dark:text-blue-400',
    endpoints: [
      { method: 'POST', path: '/api/v1/chat', desc: 'Send a message, get AI reply (text, no phone call)' },
      { method: 'POST', path: '/api/v1/chat/sessions', desc: 'Create a multi-turn session' },
      { method: 'GET',  path: '/api/v1/chat/sessions/{session_id}', desc: 'Get session history' },
    ],
  },
  {
    group: 'TTS', color: 'text-emerald-600 dark:text-emerald-400',
    endpoints: [
      { method: 'POST', path: '/api/v1/tts/synthesize', desc: 'Convert text to speech (base64 MP3/WAV)' },
      { method: 'GET',  path: '/api/v1/tts/voices', desc: 'List available voices' },
    ],
  },
  {
    group: 'Outbound Calls', color: 'text-orange-600 dark:text-orange-400',
    endpoints: [
      { method: 'POST',   path: '/api/v1/outbound/call', desc: 'Trigger AI voice call to a phone number' },
      { method: 'GET',    path: '/api/v1/outbound/calls/{id}/status', desc: 'Get call status, transcript, summary' },
      { method: 'GET',    path: '/api/v1/outbound/calls', desc: 'List all your outbound calls' },
      { method: 'DELETE', path: '/api/v1/outbound/calls/{id}', desc: 'Cancel a queued call' },
      { method: 'GET',    path: '/api/v1/outbound/usage', desc: 'Quota usage for outbound calls' },
    ],
  },
  {
    group: 'Tickets', color: 'text-amber-600 dark:text-amber-400',
    endpoints: [
      { method: 'POST',  path: '/api/v1/tickets', desc: 'Create an IT ticket' },
      { method: 'GET',   path: '/api/v1/tickets', desc: 'List tickets' },
      { method: 'GET',   path: '/api/v1/tickets/{ticket_id}', desc: 'Get a single ticket' },
      { method: 'PATCH', path: '/api/v1/tickets/{ticket_id}', desc: 'Update ticket' },
    ],
  },
  {
    group: 'Analytics', color: 'text-cyan-600 dark:text-cyan-400',
    endpoints: [
      { method: 'GET', path: '/api/v1/analytics/calls', desc: 'Call stats scoped to your API key' },
    ],
  },
  {
    group: 'Webhooks', color: 'text-pink-600 dark:text-pink-400',
    endpoints: [
      { method: 'POST',   path: '/api/v1/webhooks', desc: 'Register a webhook URL' },
      { method: 'GET',    path: '/api/v1/webhooks', desc: 'List registered webhooks' },
      { method: 'DELETE', path: '/api/v1/webhooks/{webhook_id}', desc: 'Remove a webhook' },
    ],
  },
];

const METHOD_COLORS: Record<string, string> = {
  GET: 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  POST: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
  PATCH: 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
  DELETE: 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300',
};

function EndpointReference({ activeKey }: { activeKey?: string }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-xl border border-stone-200 dark:border-white/10 bg-white dark:bg-white/5 overflow-hidden">
      <button
        onClick={() => setOpen(s => !s)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-stone-50 dark:hover:bg-white/5 transition-colors"
      >
        <div className="flex items-center gap-2">
          <ExternalLink className="w-4 h-4 text-stone-500" />
          <span className="text-sm font-semibold text-stone-900 dark:text-white">API Reference</span>
          <span className="text-[10px] text-stone-400">19 endpoints</span>
        </div>
        {open ? <ChevronUp className="w-4 h-4 text-stone-400" /> : <ChevronDown className="w-4 h-4 text-stone-400" />}
      </button>

      {open && (
        <div className="border-t border-stone-100 dark:border-white/10">
          {activeKey && (
            <div className="px-4 py-3 bg-stone-50 dark:bg-white/5 border-b border-stone-100 dark:border-white/10">
              <p className="text-[10px] text-stone-500 dark:text-stone-400 mb-1">Your token (for Postman):</p>
              <code className="text-[10px] font-mono text-stone-700 dark:text-stone-300 break-all select-all">{activeKey}</code>
            </div>
          )}
          <div className="divide-y divide-stone-100 dark:divide-white/5">
            {ENDPOINT_GROUPS.map(({ group, color, endpoints }) => (
              <div key={group} className="px-4 py-3">
                <p className={`text-[10px] font-bold uppercase tracking-wider mb-2 ${color}`}>{group}</p>
                <div className="space-y-1.5">
                  {endpoints.map(({ method, path, desc }) => (
                    <div key={path} className="flex items-start gap-2">
                      <span className={`flex-shrink-0 px-1.5 py-0.5 rounded text-[10px] font-bold font-mono ${METHOD_COLORS[method]}`}>{method}</span>
                      <div>
                        <code className="text-[11px] font-mono text-stone-700 dark:text-stone-300">{path}</code>
                        <p className="text-[10px] text-stone-400 dark:text-stone-500">{desc}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <div className="px-4 py-3 bg-stone-50 dark:bg-white/5 border-t border-stone-100 dark:border-white/10">
            <a href={`${API_BASE}/docs`} target="_blank" rel="noopener noreferrer"
              className="flex items-center gap-1.5 text-xs text-stone-600 dark:text-stone-400 hover:text-stone-900 dark:hover:text-white">
              <ExternalLink className="w-3.5 h-3.5" /> Open interactive Swagger UI → {API_BASE}/docs
            </a>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Main Panel ───────────────────────────────────────────────────────────────

export function APIKeysPanel() {
  const [activeKey, setActiveKey] = useState<string | null>(() => {
    if (typeof window !== 'undefined') return localStorage.getItem('stl_api_key');
    return null;
  });
  const [newKey, setNewKey] = useState<string | null>(null);
  const [view, setView] = useState<'use' | 'register'>('use');

  function handleRegistered(key: string) {
    setNewKey(key);
    setActiveKey(key);
    localStorage.setItem('stl_api_key', key);
    setView('use');
  }

  function handleRotated(key: string) {
    setNewKey(key);
    setActiveKey(key);
    localStorage.setItem('stl_api_key', key);
  }

  function handleClearKey() {
    if (!confirm('Clear stored API key from this browser? You can re-enter it anytime.')) return;
    setActiveKey(null);
    setNewKey(null);
    localStorage.removeItem('stl_api_key');
  }

  return (
    <div className="flex-1 flex flex-col h-full min-h-0 bg-[#fbfbfa] dark:bg-[#08090e] overflow-y-auto">
      {/* Header */}
      <div className="px-6 py-5 border-b border-stone-200 dark:border-white/10 flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-stone-100 dark:bg-white/10 flex items-center justify-center">
            <KeyRound className="w-4 h-4 text-stone-600 dark:text-stone-400" />
          </div>
          <div>
            <h1 className="text-base font-semibold text-stone-900 dark:text-white">API Keys</h1>
            <p className="text-xs text-stone-500 dark:text-stone-400">Manage your developer API tokens and integrations</p>
          </div>
        </div>
      </div>

      <div className="flex-1 px-6 py-5 space-y-5 max-w-3xl">

        {/* New Key Banner */}
        {newKey && (
          <NewKeyBanner apiKey={newKey} onDismiss={() => setNewKey(null)} />
        )}

        {/* Register / Use toggle */}
        {!activeKey && (
          <div>
            <div className="flex gap-2 mb-4">
              <button onClick={() => setView('register')}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${view === 'register' ? 'bg-stone-900 dark:bg-white text-white dark:text-stone-900' : 'text-stone-600 dark:text-stone-400 hover:bg-stone-100 dark:hover:bg-white/10'}`}>
                Generate new key
              </button>
              <button onClick={() => setView('use')}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${view === 'use' ? 'bg-stone-900 dark:bg-white text-white dark:text-stone-900' : 'text-stone-600 dark:text-stone-400 hover:bg-stone-100 dark:hover:bg-white/10'}`}>
                I already have a key
              </button>
            </div>

            {view === 'register' && (
              <div className="rounded-xl border border-stone-200 dark:border-white/10 bg-white dark:bg-white/5 p-4">
                <h2 className="text-sm font-semibold text-stone-900 dark:text-white mb-3">Generate API Key</h2>
                <RegisterForm onRegistered={handleRegistered} />
              </div>
            )}

            {view === 'use' && (
              <div className="rounded-xl border border-stone-200 dark:border-white/10 bg-white dark:bg-white/5 p-4">
                <h2 className="text-sm font-semibold text-stone-900 dark:text-white mb-3">Enter your API Key</h2>
                <ExistingKeyForm onKey={key => { setActiveKey(key); localStorage.setItem('stl_api_key', key); }} />
              </div>
            )}
          </div>
        )}

        {/* Profile + Quotas */}
        {activeKey && (
          <>
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-stone-900 dark:text-white">Your API Client</h2>
              <button onClick={handleClearKey} className="text-xs text-stone-400 hover:text-stone-600 dark:hover:text-stone-300">
                Clear key
              </button>
            </div>
            <MyProfileCard apiKey={activeKey} onRotated={handleRotated} />
            <WebhookManager apiKey={activeKey} />
          </>
        )}

        {/* API Reference — always shown */}
        <EndpointReference activeKey={activeKey || undefined} />
      </div>
    </div>
  );
}

// Small sub-component for existing-key entry
function ExistingKeyForm({ onKey }: { onKey: (k: string) => void }) {
  const [val, setVal] = useState('');
  return (
    <div className="flex gap-2">
      <input
        value={val} onChange={e => setVal(e.target.value)}
        placeholder="stl_outbound_..."
        type="password"
        className="flex-1 px-3 py-2 text-sm rounded-lg border border-stone-200 dark:border-white/10 bg-white dark:bg-white/5 text-stone-900 dark:text-white placeholder-stone-400 font-mono focus:outline-none focus:ring-2 focus:ring-stone-400"
      />
      <button
        onClick={() => val.startsWith('stl_outbound_') ? onKey(val.trim()) : alert('Key must start with stl_outbound_')}
        className="px-4 py-2 rounded-lg bg-stone-900 dark:bg-white text-white dark:text-stone-900 text-sm font-medium hover:bg-stone-700 dark:hover:bg-stone-100"
      >Use</button>
    </div>
  );
}
