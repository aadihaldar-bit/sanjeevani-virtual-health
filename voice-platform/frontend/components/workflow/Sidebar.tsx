import React, { useState, useEffect } from 'react';
import { Cpu, FileText, Mic, Phone, Play, UploadCloud } from 'lucide-react';
import { authFetch } from '@/lib/authFetch';
import { getApiBase } from '@/lib/api';
import { useWorkspace } from '@/lib/useMe';
import { HELP, INPUT, LABEL, PANEL_HEAD } from './styles';

const FALLBACK_VOICES = ['Aarohi', 'Radhika', 'Shweta', 'Nalini', 'Deepak', 'Gaurav', 'Hemraj'];

// Mirrors the backend: a tool node is a ticket tool if its name says so, otherwise it transfers the call.
function toolIdFor(data: any): string {
  const raw = String(data.toolName || data.tool_name || '').toLowerCase();
  return raw.includes('ticket') ? 'create_symphony_ticket' : 'transfer_to_human';
}

export default function Sidebar({
  node,
  updateNode,
  options,
  ttsProvider,
  geminiLive = false,
}: {
  node: any;
  updateNode: (id: string, data: any) => void;
  options?: any;
  ttsProvider?: string;
  geminiLive?: boolean;
}) {
  const { isPlatform } = useWorkspace();
  const [activeTab, setActiveTab] = useState<'general' | 'knowledge'>('general');
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [availableDocs, setAvailableDocs] = useState<any[]>([]);

  const data = node.data || {};

  const loadDocs = async () => {
    try {
      const res = await authFetch(`${getApiBase()}/api/knowledge/documents`);
      if (res.ok) setAvailableDocs((await res.json()).documents || []);
    } catch (e) {
      console.error('Failed to load knowledge documents for the step panel', e);
    }
  };
  useEffect(() => { loadDocs(); }, []);

  const handleChange = (field: string, value: any) => updateNode(node.id, { [field]: value });

  const handleUpload = async () => {
    if (!file) return;
    setUploading(true);
    setUploadError('');
    const formData = new FormData();
    formData.append('file', file);
    formData.append('node_id', node.id);
    try {
      const res = await authFetch(`${getApiBase()}/api/knowledge/upload`, { method: 'POST', body: formData });
      if (res.ok) {
        updateNode(node.id, { use_rag: true, kb_file: file.name });
        await loadDocs();
        setFile(null);
      } else {
        setUploadError('The document could not be uploaded.');
      }
    } catch (e) {
      console.error(e);
      setUploadError('The document could not be uploaded. Check your connection and try again.');
    } finally {
      setUploading(false);
    }
  };

  if (node.type === 'startNode') {
    return (
      <div className={PANEL_HEAD}>
        <h2 className="text-[14px] font-semibold text-stone-900 dark:text-white flex items-center gap-2"><Play className="w-4 h-4 text-stone-400" strokeWidth={1.75} /> Call starts</h2>
        <p className="mt-2 text-[13px] leading-relaxed text-stone-500 dark:text-stone-400">
          Every call begins here. Draw a path from this step to the agent that should answer first.
        </p>
      </div>
    );
  }

  if (node.type === 'endNode') {
    return (
      <div className={PANEL_HEAD}>
        <h2 className="text-[14px] font-semibold text-stone-900 dark:text-white">End the call</h2>
        <p className="mt-2 text-[13px] leading-relaxed text-stone-500 dark:text-stone-400">The agent says goodbye and hangs up when a call reaches this step.</p>
      </div>
    );
  }

  const isTool = node.type === 'toolNode';
  const attachedAll = !data.kb_docs || data.kb_docs.length === 0;

  return (
    <div className="flex flex-col h-full">
      <div className={PANEL_HEAD}>
        <h2 className="text-[14px] font-semibold text-stone-900 dark:text-white">{isTool ? 'Action step' : 'Agent step'}</h2>
        <p className="text-[12px] text-stone-500 dark:text-stone-400 mt-0.5 truncate">{data.name || 'Untitled'}</p>
      </div>

      {!isTool && (
        <div role="tablist" className="flex border-b border-stone-200 dark:border-white/10">
          {([['general', 'Settings'], ['knowledge', `Knowledge (${availableDocs.length})`]] as const).map(([id, label]) => (
            <button key={id} role="tab" aria-selected={activeTab === id} onClick={() => setActiveTab(id)}
              className={`flex-1 py-2.5 text-[13px] font-medium cursor-pointer border-b-2 -mb-px transition-colors ${
                activeTab === id ? 'border-indigo-600 text-stone-900 dark:text-white' : 'border-transparent text-stone-500 dark:text-stone-400 hover:text-stone-800 dark:hover:text-stone-200'}`}>
              {label}
            </button>
          ))}
        </div>
      )}

      <div className="p-4 flex-grow overflow-y-auto">
        {(activeTab === 'knowledge' && !isTool) ? (
          <div className="space-y-4">
            <label className="flex items-start justify-between gap-3 p-3 rounded-xl border border-stone-200 dark:border-white/10 bg-stone-50 dark:bg-white/5 cursor-pointer">
              <span>
                <span className="block text-[13px] font-medium text-stone-900 dark:text-white">Answer from the knowledge base</span>
                <span className="block mt-0.5 text-[12px] text-stone-500 dark:text-stone-400">The agent looks things up in your documents before it answers.</span>
              </span>
              <input type="checkbox" checked={data.use_rag !== false} onChange={(e) => handleChange('use_rag', e.target.checked)}
                className="mt-1 w-4 h-4 rounded border-stone-300 text-indigo-600 focus:ring-indigo-500 cursor-pointer" />
            </label>

            <div>
              <div className="flex items-center justify-between mb-2">
                <p className="text-[12px] font-medium text-stone-700 dark:text-stone-300">Documents this agent can use</p>
                <span className="text-[11px] text-stone-500 dark:text-stone-400">{attachedAll ? 'All documents' : `${data.kb_docs.length} chosen`}</span>
              </div>
              {availableDocs.length === 0 ? (
                <p className="text-[12px] text-stone-500 dark:text-stone-400 py-3">No documents yet. Upload one below, or add some under Knowledge.</p>
              ) : (
                <ul className="space-y-1.5 max-h-[260px] overflow-y-auto pr-1">
                  {availableDocs.map((doc: any) => {
                    const on = attachedAll || data.kb_docs.includes(doc.name) || data.kb_docs.includes(doc.id);
                    return (
                      <li key={doc.id}>
                        <button type="button" aria-pressed={on}
                          onClick={() => {
                            const current: string[] = attachedAll ? availableDocs.map((d: any) => d.name) : data.kb_docs;
                            handleChange('kb_docs', current.includes(doc.name) ? current.filter((d) => d !== doc.name) : [...current, doc.name]);
                          }}
                          className={`w-full text-left p-2.5 rounded-lg border flex items-start gap-2.5 cursor-pointer transition-colors ${
                            on ? 'border-indigo-200 dark:border-indigo-400/30 bg-indigo-50/60 dark:bg-indigo-500/10' : 'border-stone-200 dark:border-white/10 opacity-60 hover:opacity-100'}`}>
                          <FileText className={`w-4 h-4 mt-0.5 flex-shrink-0 ${on ? 'text-indigo-600 dark:text-indigo-300' : 'text-stone-400'}`} strokeWidth={1.75} />
                          <span className="min-w-0 flex-1">
                            <span className="block text-[13px] font-medium text-stone-900 dark:text-white truncate" title={doc.name}>{doc.name}</span>
                            <span className="block text-[11px] text-stone-500 dark:text-stone-400">{doc.size_kb} KB · {doc.chunks || 0} {doc.chunks === 1 ? 'section' : 'sections'}</span>
                          </span>
                          <span className="text-[11px] text-stone-500 dark:text-stone-400 mt-0.5">{on ? 'Used' : 'Not used'}</span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            <div className="rounded-xl border border-dashed border-stone-300 dark:border-white/15 p-4 text-center">
              <UploadCloud className="w-5 h-5 mx-auto text-stone-400 mb-1.5" strokeWidth={1.75} />
              <p className="text-[12px] text-stone-500 dark:text-stone-400 mb-2">Add a document for this step</p>
              <input type="file" id="step-file" className="hidden" accept=".md,.txt,.pdf" onChange={(e) => setFile(e.target.files ? e.target.files[0] : null)} />
              <label htmlFor="step-file" className="inline-block h-8 leading-8 px-3 rounded-lg border border-stone-300 dark:border-white/15 bg-white dark:bg-white/5 text-[13px] text-stone-800 dark:text-stone-100 cursor-pointer hover:bg-stone-50 dark:hover:bg-white/10 max-w-full truncate">
                {file ? file.name : 'Choose a file (.pdf, .md, .txt)'}
              </label>
              {file && (
                <button onClick={handleUpload} disabled={uploading}
                  className="mt-2.5 block w-full h-8 rounded-lg bg-indigo-600 text-white text-[13px] font-medium hover:bg-indigo-500 disabled:opacity-50 cursor-pointer">
                  {uploading ? 'Adding...' : 'Upload and use it here'}
                </button>
              )}
              {uploadError && <p role="alert" className="mt-2 text-[12px] text-rose-600 dark:text-rose-400">{uploadError}</p>}
            </div>
          </div>
        ) : (
          <div className="space-y-5">
            <div>
              <label className={LABEL} htmlFor="step-name">Name</label>
              <input id="step-name" type="text" value={data.name || ''} onChange={(e) => handleChange('name', e.target.value)}
                className={INPUT} placeholder={isTool ? 'e.g. Hand over to billing' : 'e.g. Appointment booking'} />
            </div>

            {isTool && (
              <div>
                <label className={LABEL} htmlFor="step-tool">What should this step do?</label>
                <select id="step-tool" value={toolIdFor(data)} onChange={(e) => handleChange('toolName', e.target.value)} className={INPUT}>
                  {(options?.tools || [
                    { id: 'transfer_to_human', label: 'Hand the call to a person', help: '' },
                    { id: 'create_symphony_ticket', label: 'Create a ticket', help: '' },
                  ]).map((t: any) => (
                    <option key={t.id} value={t.id}>{t.label}</option>
                  ))}
                </select>
                <p className={HELP}>{(options?.tools || []).find((t: any) => t.id === toolIdFor(data))?.help}</p>
              </div>
            )}

            {isTool && toolIdFor(data) === 'transfer_to_human' && (
              <div className="p-3 rounded-xl border border-amber-200 dark:border-amber-400/30 bg-amber-50/70 dark:bg-amber-500/10">
                <label className="flex items-center gap-1.5 text-[12px] font-medium text-amber-900 dark:text-amber-200 mb-1.5" htmlFor="step-number">
                  <Phone className="w-3.5 h-3.5" strokeWidth={1.75} /> Number to transfer to
                </label>
                <input id="step-number" type="text" value={data.transfer_number || data.phone_number || ''}
                  onChange={(e) => { handleChange('transfer_number', e.target.value); handleChange('phone_number', e.target.value); }}
                  className={`${INPUT} font-mono`} placeholder="Leave empty to use your handover number" />
                <p className="mt-1.5 text-[11.5px] leading-snug text-amber-900/80 dark:text-amber-200/80">
                  Use international format, like +919876543210. When empty, calls go to the handover number set for your organization in Settings. If none is set, the agent takes a message instead.
                </p>
              </div>
            )}

            {!isTool && (
              <div>
                <label className={LABEL} htmlFor="step-prompt">Instructions</label>
                <textarea id="step-prompt" value={data.system_prompt || ''} onChange={(e) => handleChange('system_prompt', e.target.value)}
                  className={`${INPUT} h-36 resize-none leading-relaxed`}
                  placeholder="Who this agent speaks for, what it should find out, and what it must never do." />
              </div>
            )}

            {!isTool && geminiLive && (() => {
              const voices: { id: string; label: string }[] = options?.gemini_live?.voices || [];
              const chosen = voices.find((v) => v.id.toLowerCase() === String(data.voice || '').toLowerCase())?.id || '';
              return (
                <div>
                  <label className={`${LABEL} flex items-center gap-1.5`} htmlFor="step-gemini-voice">
                    <Mic className="w-3.5 h-3.5 text-stone-400" strokeWidth={1.75} /> Voice
                  </label>
                  <select id="step-gemini-voice" value={chosen} onChange={(e) => handleChange('voice', e.target.value)} className={INPUT}>
                    <option value="">Default voice</option>
                    {voices.map((v) => (<option key={v.id} value={v.id}>{v.label}</option>))}
                  </select>
                  <p className={HELP}>
                    This agent's calls run on Gemini Live, which speaks and listens directly. Gentle and warm voices suit health care; each step can have its own. Changes apply to the next call.
                  </p>
                </div>
              );
            })()}

            {!isTool && !geminiLive && (
              <div>
                {(() => {
                  const providerId = ttsProvider || options?.defaults?.tts?.provider || '';
                  const provider = options?.tts?.find((p: any) => p.id === providerId);
                  const voices: { id: string; label: string }[] =
                    provider?.voices?.filter((v: any) => v.id) || FALLBACK_VOICES.map((v) => ({ id: v, label: v }));
                  const current = data.voice || '';
                  const known = !current || voices.some((v) => v.id.toLowerCase() === String(current).toLowerCase());
                  return (
                    <>
                      <label className={`${LABEL} flex items-center gap-1.5`} htmlFor="step-voice">
                        <Mic className="w-3.5 h-3.5 text-stone-400" strokeWidth={1.75} /> Voice{provider ? ` (${provider.label.split(' (')[0]})` : ''}
                      </label>
                      {provider && provider.voices?.every((v: any) => !v.id) ? (
                        <p className={HELP}>This voice engine uses one default voice.</p>
                      ) : (
                        <select id="step-voice" value={current} onChange={(e) => handleChange('voice', e.target.value)} className={INPUT}>
                          <option value="">Default voice</option>
                          {!known && <option value={current}>{current} (not offered by this engine)</option>}
                          {voices.map((v) => (<option key={v.id} value={v.id}>{v.label}</option>))}
                        </select>
                      )}
                      {provider && !provider.available && (
                        <p className="mt-1 text-[11.5px] text-amber-700 dark:text-amber-300">This voice engine is not set up, so calls will use the default one.</p>
                      )}
                    </>
                  );
                })()}
              </div>
            )}

            {node.type === 'agentNode' && (
              <div>
                <label className={LABEL} htmlFor="step-greeting">Opening line</label>
                <textarea id="step-greeting" value={data.greeting || ''} onChange={(e) => handleChange('greeting', e.target.value)} rows={2}
                  className={`${INPUT} resize-none`} placeholder="What callers hear first, if this agent answers first. Empty uses the default greeting." />
              </div>
            )}

            {node.type === 'agentNode' && (
              <div>
                <label className={LABEL} htmlFor="step-role">Role on a call</label>
                <select id="step-role" value={data.role || ''} onChange={(e) => handleChange('role', e.target.value)} className={INPUT}>
                  <option value="">Standard (one agent for the whole call)</option>
                  <option value="orchestrator">Front desk (answers a call that comes in and hands the caller on)</option>
                  <option value="specialist">Specialist (does one job when the front desk hands a caller over)</option>
                  <option value="outbound">Outbound (answers a call we place)</option>
                </select>
                <p className={HELP}>
                  For agents that run on Gemini Live. With a front desk, the caller is passed along the lines you draw between steps. Each step then
                  talks with its own instructions, voice, tools and documents, and the line&apos;s condition tells the front desk when to use it.
                </p>
              </div>
            )}

            {node.type === 'agentNode' && isPlatform && (
              <div>
                <label className={LABEL} htmlFor="step-guided">Guided conversation</label>
                <select id="step-guided" value={data.domain || ''} onChange={(e) => handleChange('domain', e.target.value)} className={INPUT}>
                  <option value="">Standard (the agent decides freely)</option>
                  <option value="incident">IT incident intake (asks the right questions, then raises a ticket)</option>
                  <option value="medical">Medical receptionist (triage, doctor, insurance, booking)</option>
                </select>
                <p className={HELP}>A guided conversation collects what is needed step by step and never skips a required question.</p>
              </div>
            )}

            {node.type === 'agentNode' && (options?.agent_tools?.length ?? 0) > 0 && (
              <fieldset>
                <legend className={LABEL}>Tools this step may use</legend>
                <ul className="space-y-1.5">
                  {options.agent_tools.map((t: any) => {
                    const chosen: string[] = data.tools || [];
                    const on = chosen.includes(t.id);
                    return (
                      <li key={t.id}>
                        <label className="flex items-start gap-2.5 p-2 rounded-lg border border-stone-200 dark:border-white/10 cursor-pointer hover:bg-stone-50 dark:hover:bg-white/5">
                          <input type="checkbox" checked={on} className="mt-0.5 w-4 h-4 rounded border-stone-300 text-indigo-600 focus:ring-indigo-500"
                            onChange={(e) => handleChange('tools', e.target.checked ? [...chosen, t.id] : chosen.filter((x) => x !== t.id))} />
                          <span className="min-w-0">
                            <span className="block text-[13px] font-medium text-stone-900 dark:text-white">{t.label}</span>
                            <span className="block text-[11.5px] leading-snug text-stone-500 dark:text-stone-400">{t.help}</span>
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
                <p className={HELP}>Leave all unticked and the agent uses the standard tools for its kind of work.</p>
              </fieldset>
            )}

            {!isTool && (
              <div>
                <label className={`${LABEL} flex items-center gap-1.5`}>
                  <Cpu className="w-3.5 h-3.5 text-stone-400" strokeWidth={1.75} /> Language model
                </label>
                {options ? (
                  <div className="grid grid-cols-2 gap-2">
                    <select value={data.llm || ''} aria-label="Language model provider" className={INPUT}
                      onChange={(e) => { handleChange('llm', e.target.value); handleChange('llm_model', ''); }}>
                      <option value="">Agent default</option>
                      {options.llm.map((p: any) => (
                        <option key={p.id} value={p.id} disabled={!p.available || p.healthy === false}>
                          {!p.available ? `${p.label} (not configured)`
                            : p.healthy === false ? `${p.label} (not working: ${p.issue || 'failed a test request'})` : p.label}
                        </option>
                      ))}
                    </select>
                    <select value={data.llm_model || ''} disabled={!data.llm} aria-label="Language model" className={INPUT}
                      onChange={(e) => handleChange('llm_model', e.target.value)}>
                      <option value="">Default model</option>
                      {(options.llm.find((p: any) => p.id === data.llm)?.models || []).map((m: string) => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </select>
                  </div>
                ) : (
                  <p className={HELP}>Loading available models...</p>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
