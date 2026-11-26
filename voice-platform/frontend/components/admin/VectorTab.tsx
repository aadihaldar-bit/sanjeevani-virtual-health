'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Database } from 'lucide-react';
import { api, type Me, type VectorStore } from '@/lib/adminApi';
import { Badge, Btn, Card, Field, Notice, OrgPicker, ago, inputCls, useAction, useOrgScope } from './ui';

interface Info { config: VectorStore | null; embedding_dim: number | null; encryption_configured: boolean; }
interface TestResult { ok: boolean; error: string | null; vector_size: number | null; collection_exists: boolean | null; will_create: boolean; ms: number; }

const BLANK = { url: '', api_key: '', collection: '', mode: 'index' as 'index' | 'read_only', text_field: 'text', source_field: '', enabled: true };

export function VectorTab({ me, scope }: { me: Me; scope: ReturnType<typeof useOrgScope> }) {
  const [info, setInfo] = useState<Info | null>(null);
  const [form, setForm] = useState(BLANK);
  const [test, setTest] = useState<TestResult | null>(null);
  const [counts, setCounts] = useState<{ indexed: number; removed: number; documents: number } | null>(null);
  const action = useAction();
  const { orgId } = scope;
  const q = me.is_super_admin && orgId !== null ? `?tenant_id=${orgId}` : '';

  const load = useCallback(async () => {
    try {
      const data = await api<Info>(`/api/vector-stores${q}`);
      setInfo(data);
      const c = data.config;
      setForm(c ? { url: c.url, api_key: '', collection: c.collection, mode: c.mode, text_field: c.text_field || 'text', source_field: c.source_field || '', enabled: c.enabled } : BLANK);
    } catch { setInfo(null); }
    setTest(null); setCounts(null);
  }, [q]);
  useEffect(() => { void load(); }, [load]);

  const cfg = info?.config || null;
  const save = async () => {
    const ok = await action.run(() => api('/api/vector-stores', { method: 'PUT', body: {
      tenant_id: orgId, provider: 'qdrant', url: form.url, api_key: form.api_key || null, collection: form.collection, mode: form.mode,
      text_field: form.mode === 'read_only' ? form.text_field : null, source_field: form.mode === 'read_only' ? (form.source_field || null) : null,
      enabled: form.enabled } }), 'Saved. Run the connection test next.');
    if (ok) await load();
  };
  const runTest = async () => {
    const r = await action.run(() => api<TestResult>(`/api/vector-stores/test${q}`, { method: 'POST' }));
    if (r) setTest(r);
    const data = await api<Info>(`/api/vector-stores${q}`).catch(() => null);
    if (data) setInfo(data);
  };
  const reindex = async () => {
    const r = await action.run(() => api<{ indexed: number; removed: number; documents: number }>(`/api/vector-stores/reindex${q}`, { method: 'POST' }), 'Indexing finished.');
    if (r) setCounts(r);
  };
  const remove = async () => {
    if (!window.confirm('Remove the external vector store? Retrieval goes back to the platform store. Your index is not touched.')) return;
    await action.run(() => api(`/api/vector-stores${q}`, { method: 'DELETE' }), 'Removed.');
    await load();
  };

  return (
    <div>
      <OrgPicker me={me} scope={scope} />
      {info && !info.encryption_configured && (
        <Notice kind="error"><strong>Secrets cannot be stored yet.</strong> The server has no <code>CREDENTIAL_ENCRYPTION_KEY</code>.</Notice>
      )}
      <Card title="External vector database" subtitle="Point your organization's knowledge retrieval at your own Qdrant instead of the platform's. If it is slow or down, calls simply continue without it."
        actions={cfg && <Badge tone={cfg.last_test_ok === true ? 'green' : cfg.last_test_ok === false ? 'red' : 'grey'}>{cfg.last_test_ok === true ? 'connected' : cfg.last_test_ok === false ? 'failing' : 'untested'}</Badge>}>
        {action.error && <Notice kind="error" onClose={action.clear}>{action.error}</Notice>}
        {action.done && <Notice kind="success" onClose={action.clear}>{action.done}</Notice>}

        <fieldset className="mb-4">
          <legend className="text-[11px] font-medium text-stone-600 dark:text-stone-300 mb-2">How should it be used?</legend>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-w-3xl">
            {([['index', 'Store our knowledge there', 'We write our knowledge vectors into a collection in your database and search them there. Passage text stays in the platform.'],
               ['read_only', 'Search an index you already have', 'We only query your existing collection and use the text stored in each point. Nothing is ever written to it.']] as const).map(([value, title, text]) => (
              <label key={value} className={`rounded-lg border p-3 text-xs cursor-pointer ${form.mode === value ? 'border-indigo-500 bg-indigo-50/60 dark:bg-indigo-950/20' : 'border-stone-200 dark:border-white/10'}`}>
                <input type="radio" name="mode" className="mr-2" checked={form.mode === value} onChange={() => setForm({ ...form, mode: value })} />
                <span className="font-semibold">{title}</span>
                <span className="block text-[11px] text-stone-500 dark:text-stone-400 mt-1">{text}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-3xl">
          <div className="sm:col-span-2"><Field label="Qdrant URL" hint="https only, publicly reachable (private network addresses are refused)."><input className={inputCls} value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://xyz.cloud.qdrant.io:6333" /></Field></div>
          <Field label={cfg?.has_api_key ? 'API key (leave empty to keep the saved one)' : 'API key'}><input className={inputCls} type="password" autoComplete="off" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} /></Field>
          <Field label="Collection" hint={form.mode === 'index' && info?.embedding_dim ? `Stored as <name>_${info.embedding_dim} to match our ${info.embedding_dim}-dimension embeddings.` : undefined}>
            <input className={inputCls} value={form.collection} onChange={(e) => setForm({ ...form, collection: e.target.value })} placeholder="company_knowledge" /></Field>
          {form.mode === 'read_only' && (
            <>
              <Field label="Payload field with the passage text" hint="The field in each point that holds the text to read to the caller."><input className={inputCls} value={form.text_field} onChange={(e) => setForm({ ...form, text_field: e.target.value })} /></Field>
              <Field label="Payload field naming the source (optional)"><input className={inputCls} value={form.source_field} onChange={(e) => setForm({ ...form, source_field: e.target.value })} /></Field>
            </>
          )}
        </div>
        <label className="flex items-center gap-2 text-xs mt-3"><input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /> Use this store for calls</label>

        <div className="flex flex-wrap items-center gap-2 mt-4">
          <Btn onClick={save} busy={action.busy} disabled={!form.url || !form.collection || info?.encryption_configured === false}><Database className="w-3 h-3" /> Save</Btn>
          {cfg && <Btn kind="ghost" onClick={runTest} busy={action.busy}>Test connection</Btn>}
          {cfg && cfg.mode === 'index' && <Btn kind="ghost" onClick={reindex} busy={action.busy}>Index our knowledge now</Btn>}
          {cfg && <Btn kind="danger" onClick={remove}>Remove</Btn>}
        </div>

        {test && (
          <div className="mt-4"><Notice kind={test.ok ? 'success' : 'error'}>
            {test.ok
              ? <>Connected in {Math.round(test.ms)} ms. {test.will_create ? 'The collection does not exist yet and will be created when you index.' : `Collection found with ${test.vector_size}-dimension vectors.`}</>
              : <>{test.error}</>}
          </Notice></div>
        )}
        {counts && <p className="text-[11px] text-stone-600 dark:text-stone-300 mt-2">{counts.documents} documents · {counts.indexed} newly indexed · {counts.removed} removed</p>}
        {cfg?.last_tested_at && !test && <p className="text-[11px] text-stone-400 mt-3">Last tested {ago(cfg.last_tested_at)}{cfg.last_test_error ? `: ${cfg.last_test_error}` : ''}</p>}
        <p className="text-[10px] text-stone-400 mt-3">Our embedding model produces {info?.embedding_dim ?? '?'}-dimension vectors. A collection with a different size is refused.</p>
      </Card>
    </div>
  );
}
