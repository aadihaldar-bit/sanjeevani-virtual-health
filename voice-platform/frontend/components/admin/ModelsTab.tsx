'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { KeyRound, ShieldAlert, Trash2 } from 'lucide-react';
import { api, type Credential, type Me, type ModelEndpoint } from '@/lib/adminApi';
import { Badge, Btn, Card, Field, Notice, OrgPicker, Table, ago, inputCls, useAction, useOrgScope } from './ui';

interface Support { kind: string; provider: string; }
const KIND_LABEL: Record<string, string> = { llm: 'Language model', stt: 'Speech to text', tts: 'Text to speech' };
const EMPTY_ENDPOINT = { name: '', base_url: '', model: '', api_key: '', headers: '', supports_tools: false, acknowledge: false };

/** "Name: value" lines to the object the API expects. */
function parseHeaders(text: string): Record<string, string> | undefined {
  const out: Record<string, string> = {};
  text.split('\n').map((l) => l.trim()).filter(Boolean).forEach((line) => {
    const i = line.indexOf(':');
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  });
  return Object.keys(out).length ? out : undefined;
}

export function ModelsTab({ me, scope }: { me: Me; scope: ReturnType<typeof useOrgScope> }) {
  const [encryption, setEncryption] = useState<boolean | null>(null);
  const [supported, setSupported] = useState<Support[]>([]);
  const [creds, setCreds] = useState<Credential[]>([]);
  const [endpoints, setEndpoints] = useState<ModelEndpoint[]>([]);
  const [cred, setCred] = useState({ kind: 'llm', provider: 'groq', api_key: '' });
  const [ep, setEp] = useState(EMPTY_ENDPOINT);
  const [platformWide, setPlatformWide] = useState(false);
  const [testResult, setTestResult] = useState<Record<number, string>>({});
  const [keyResult, setKeyResult] = useState<Record<number, { ok: boolean; text: string }>>({});
  const credAction = useAction();
  const epAction = useAction();
  const { orgId } = scope;
  const q = me.is_super_admin && orgId !== null ? `?tenant_id=${orgId}` : '';

  const load = useCallback(async () => {
    try {
      const s = await api<{ encryption_configured: boolean; supported_credentials: Support[] }>('/api/external/status');
      setEncryption(s.encryption_configured); setSupported(s.supported_credentials);
    } catch { setEncryption(null); }
    try { setCreds((await api<{ credentials: Credential[] }>(`/api/external/credentials${q}`)).credentials); } catch { setCreds([]); }
    try { setEndpoints((await api<{ endpoints: ModelEndpoint[] }>(`/api/external/endpoints${q}`)).endpoints); } catch { setEndpoints([]); }
  }, [q]);
  useEffect(() => { void load(); }, [load]);

  const providersFor = (kind: string) => supported.filter((s) => s.kind === kind).map((s) => s.provider);

  const saveCred = async () => {
    const ok = await credAction.run(() => api('/api/external/credentials', { method: 'PUT', body: { tenant_id: orgId, ...cred } }), 'Key saved. Use Test key to confirm it works, then choose that provider in an agent’s settings.');
    if (ok) { setCred({ ...cred, api_key: '' }); await load(); }
  };
  const testCred = async (c: Credential) => {
    const r = await credAction.run(() => api<{ supported: boolean; ok: boolean | null; issue: string | null; model?: string }>(`/api/external/credentials/${c.id}/test`, { method: 'POST' }));
    if (!r) return;
    setKeyResult((k) => ({ ...k, [c.id]: r.ok ? { ok: true, text: `Works${r.model ? ` (${r.model})` : ''}` } : { ok: false, text: r.issue || 'Failed' } }));
  };
  const deleteCred = async (c: Credential) => {
    if (!window.confirm(`Remove the ${c.provider} key? Calls will use the platform key again.`)) return;
    await credAction.run(() => api(`/api/external/credentials/${c.id}`, { method: 'DELETE' }));
    await load();
  };
  const saveEndpoint = async () => {
    const ok = await epAction.run(() => api('/api/external/endpoints', { method: 'POST', body: {
      tenant_id: orgId, platform_wide: platformWide, name: ep.name, base_url: ep.base_url, model: ep.model,
      api_key: ep.api_key || null, headers: parseHeaders(ep.headers) ?? null, supports_tools: ep.supports_tools,
      acknowledge_data_sharing: ep.acknowledge } }), 'Endpoint added. Test it, then choose it as the language model in an agent\'s settings.');
    if (ok) { setEp(EMPTY_ENDPOINT); await load(); }
  };
  const testEndpoint = async (e: ModelEndpoint) => {
    const r = await epAction.run(() => api<{ ok: boolean; ms: number; error: string | null }>(`/api/external/endpoints/${e.id}/test`, { method: 'POST' }));
    if (r) setTestResult((t) => ({ ...t, [e.id]: r.ok ? `Working (${Math.round(r.ms)} ms)` : `Failed: ${r.error}` }));
    await load();
  };
  const toggleEndpoint = async (e: ModelEndpoint) => {
    await epAction.run(() => api(`/api/external/endpoints/${e.id}`, { method: 'PUT', body: {
      name: e.name, base_url: e.base_url, model: e.model, supports_tools: e.supports_tools, enabled: !e.enabled, acknowledge_data_sharing: true } }));
    await load();
  };
  const deleteEndpoint = async (e: ModelEndpoint) => {
    if (!window.confirm(`Delete the endpoint "${e.name}"? Agents that use it fall back to the default model.`)) return;
    await epAction.run(() => api(`/api/external/endpoints/${e.id}`, { method: 'DELETE' }));
    await load();
  };

  return (
    <div>
      <OrgPicker me={me} scope={scope} />
      {encryption === false && (
        <Notice kind="error">
          <strong>Secrets cannot be stored yet.</strong> The server has no <code>CREDENTIAL_ENCRYPTION_KEY</code>. Ask the platform administrator to set one; nothing is saved without it.
        </Notice>
      )}

      <Card title="Your own provider keys" subtitle="A key you add here replaces the platform's key for your organization's calls only. Keys are encrypted and can never be read back.">
        {credAction.error && <Notice kind="error" onClose={credAction.clear}>{credAction.error}</Notice>}
        {credAction.done && <Notice kind="success" onClose={credAction.clear}>{credAction.done}</Notice>}
        <Table head={['Type', 'Provider', 'Key', 'Updated', '']} empty="No keys of your own: the platform's keys are used.">
          {creds.map((c) => (
            <tr key={c.id}>
              <td className="py-2 pr-3">{KIND_LABEL[c.kind] || c.kind}</td>
              <td className="py-2 pr-3 font-medium capitalize">{c.provider}</td>
              <td className="py-2 pr-3 font-mono text-stone-500">{c.key_hint || 'stored'}</td>
              <td className="py-2 pr-3">{ago(c.updated_at)}</td>
              <td className="py-2 text-right whitespace-nowrap">
                {keyResult[c.id] && <span className={`mr-2 text-[11px] ${keyResult[c.id].ok ? 'text-emerald-600' : 'text-rose-600'}`}>{keyResult[c.id].text}</span>}
                {c.kind === 'llm' && <><Btn kind="ghost" onClick={() => testCred(c)} busy={credAction.busy}>Test key</Btn>{' '}</>}
                <button aria-label={`Remove ${c.provider} key`} onClick={() => deleteCred(c)} className="text-rose-500 hover:text-rose-700 align-middle cursor-pointer"><Trash2 className="w-3.5 h-3.5 inline" /></button></td>
            </tr>
          ))}
        </Table>
        <div className="grid grid-cols-1 sm:grid-cols-5 gap-2 items-end mt-4 max-w-4xl">
          <Field label="Type"><select className={inputCls} value={cred.kind} onChange={(e) => setCred({ kind: e.target.value, provider: providersFor(e.target.value)[0] || '', api_key: '' })}>
            {Object.entries(KIND_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
          <Field label="Provider"><select className={inputCls} value={cred.provider} onChange={(e) => setCred({ ...cred, provider: e.target.value })}>
            {providersFor(cred.kind).map((p) => <option key={p} value={p}>{p}</option>)}</select></Field>
          <div className="sm:col-span-2"><Field label="API key"><input className={inputCls} type="password" autoComplete="off" value={cred.api_key} onChange={(e) => setCred({ ...cred, api_key: e.target.value })} placeholder="Paste the key" /></Field></div>
          <Btn onClick={saveCred} busy={credAction.busy} disabled={cred.api_key.trim().length < 8 || encryption === false}><KeyRound className="w-3 h-3" /> Save key</Btn>
        </div>
      </Card>

      <Card title="Custom model endpoints" subtitle="Use your own model server, as long as it speaks the OpenAI chat format. After adding one, pick it as the language model in an agent's settings.">
        {epAction.error && <Notice kind="error" onClose={epAction.clear}>{epAction.error}</Notice>}
        {epAction.done && <Notice kind="success" onClose={epAction.clear}>{epAction.done}</Notice>}
        <Table head={['Name', 'Address', 'Model', 'Status', '']} empty="No custom endpoints.">
          {endpoints.map((e) => (
            <tr key={e.id}>
              <td className="py-2 pr-3 font-medium">{e.name}{e.tenant_id === null && <span className="ml-2"><Badge tone="blue">platform-wide</Badge></span>}</td>
              <td className="py-2 pr-3 font-mono text-[11px] break-all">{e.base_url}</td>
              <td className="py-2 pr-3">{e.model}</td>
              <td className="py-2 pr-3">
                {!e.enabled ? <Badge tone="amber">disabled</Badge> : e.last_test_ok === true ? <Badge tone="green">working</Badge> : e.last_test_ok === false ? <Badge tone="red">failing</Badge> : <Badge>untested</Badge>}
                {(testResult[e.id] || e.last_test_error) && <p className="text-[10px] text-stone-500 mt-1 max-w-xs break-words">{testResult[e.id] || e.last_test_error}</p>}
              </td>
              <td className="py-2 text-right whitespace-nowrap">
                <Btn kind="ghost" onClick={() => testEndpoint(e)} busy={epAction.busy}>Test</Btn>{' '}
                {(e.tenant_id !== null || me.is_super_admin) && <>
                  <Btn kind="ghost" onClick={() => toggleEndpoint(e)}>{e.enabled ? 'Disable' : 'Enable'}</Btn>{' '}
                  <button aria-label={`Delete ${e.name}`} onClick={() => deleteEndpoint(e)} className="text-rose-500 hover:text-rose-700 align-middle cursor-pointer"><Trash2 className="w-3.5 h-3.5 inline" /></button>
                </>}
              </td>
            </tr>
          ))}
        </Table>

        <div className="mt-5 max-w-3xl">
          <p className="text-xs font-semibold mb-2">Add an endpoint</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Name"><input className={inputCls} value={ep.name} onChange={(e) => setEp({ ...ep, name: e.target.value })} placeholder="Acme private model" /></Field>
            <Field label="Model name"><input className={inputCls} value={ep.model} onChange={(e) => setEp({ ...ep, model: e.target.value })} placeholder="llama-3.1-70b-instruct" /></Field>
            <div className="sm:col-span-2"><Field label="Base URL" hint="Must be https and publicly reachable. Addresses on private networks are refused."><input className={inputCls} value={ep.base_url} onChange={(e) => setEp({ ...ep, base_url: e.target.value })} placeholder="https://models.example.com/v1" /></Field></div>
            <Field label="API key (optional)"><input className={inputCls} type="password" autoComplete="off" value={ep.api_key} onChange={(e) => setEp({ ...ep, api_key: e.target.value })} /></Field>
            <Field label="Extra headers (optional)" hint="One per line, Name: value"><textarea className={`${inputCls} h-16 font-mono`} value={ep.headers} onChange={(e) => setEp({ ...ep, headers: e.target.value })} /></Field>
          </div>
          <label className="flex items-center gap-2 text-xs mt-3"><input type="checkbox" checked={ep.supports_tools} onChange={(e) => setEp({ ...ep, supports_tools: e.target.checked })} /> This model supports tool calling</label>
          {me.is_super_admin && <label className="flex items-center gap-2 text-xs mt-2"><input type="checkbox" checked={platformWide} onChange={(e) => setPlatformWide(e.target.checked)} /> Available to every organization</label>}
          <label className="flex items-start gap-2 text-xs mt-3 p-2.5 rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900/50">
            <ShieldAlert className="w-3.5 h-3.5 mt-0.5 text-amber-700 flex-shrink-0" />
            <span><input type="checkbox" className="mr-2" checked={ep.acknowledge} onChange={(e) => setEp({ ...ep, acknowledge: e.target.checked })} />
              I understand that what callers say and the call transcripts are sent to this external service.</span>
          </label>
          <div className="mt-3"><Btn onClick={saveEndpoint} busy={epAction.busy} disabled={!ep.name || !ep.base_url || !ep.model || !ep.acknowledge || encryption === false}>Add endpoint</Btn></div>
        </div>
      </Card>
    </div>
  );
}
