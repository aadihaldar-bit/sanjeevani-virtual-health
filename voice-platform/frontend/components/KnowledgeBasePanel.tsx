'use client';

import React, { useState, useRef, useEffect } from 'react';
import { UploadCloud, FileText, CheckCircle2, AlertCircle, Trash2, Database, Sparkles, Search, RefreshCw } from 'lucide-react';
import { uploadKnowledgeDocument } from '@/lib/api';
import { authFetch } from '@/lib/authFetch';
import { useDashboardStore } from '@/lib/store';
import { useWorkspace } from '@/lib/useMe';
import { Badge, Button, Card, EmptyState, Page } from '@/components/ui/kit';

import { getApiBase } from '@/lib/api';

interface KnowledgeDoc {
  id: string;
  name: string;
  size_kb: number;
  chunks: number;
  uploaded_at: string | null;
  semantic?: boolean;
  source?: 'upload' | 'builtin';
}

interface KbStatus {
  documents: number;
  chunks: number;
  semantic_documents: number;
  semantic_timeout_ms: number;
  embedding_model: string | null;
  vector: { enabled: boolean; ready: boolean; last_error: string | null };
}

interface SearchHit {
  doc_id: string;
  filename: string;
  title: string;
  section: string;
  text: string;
  bm25: number | null;
  vector: number | null;
  score: number;
}

interface SearchResponse {
  results: SearchHit[];
  mode: 'hybrid' | 'hybrid-org' | 'hybrid+external' | 'external' | 'keyword' | 'skipped';
  timings_ms: Record<string, number>;
}

const MODE_LABEL: Record<string, string> = {
  keyword: 'keyword search',
  hybrid: 'smart search',
  'hybrid-org': 'smart search',
  'hybrid+external': 'smart search and your connected knowledge base',
  external: 'your connected knowledge base',
};

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** "0.69 KB", "12.4 KB", "1.5 MB". The server rounds to one decimal, so a tiny file can arrive as 0 and a missing size as null. */
function formatSize(kb: number | null | undefined): string {
  if (typeof kb !== 'number' || !Number.isFinite(kb) || kb < 0) return '';
  if (kb >= 1024) return `${Number((kb / 1024).toFixed(1))} MB`;
  if (kb === 0) return 'under 0.1 KB';
  return `${Number(kb.toFixed(kb < 10 ? 2 : 1))} KB`;
}

/** The day a document was added, in India time. The server sends UTC with no zone marker, so slicing the text shows the wrong day after 18:30 UTC. */
function addedOn(iso: string | null | undefined): string {
  if (!iso) return '';
  const t = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
  if (Number.isNaN(t.getTime())) return '';
  return t.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Kolkata' });
}

function docDetails(doc: KnowledgeDoc): string {
  const added = addedOn(doc.uploaded_at);
  return [
    formatSize(doc.size_kb),
    Number.isFinite(doc.chunks) ? plural(doc.chunks, 'section') : '',
    added && `added ${added}`,
    doc.source === 'builtin' && 'built in',
  ].filter(Boolean).join(' · ');
}

function readableError(err: any, fallback: string): string {
  const raw: string = err?.message || '';
  const json = raw.slice(raw.indexOf('{'));
  try {
    const detail = JSON.parse(json)?.detail;
    if (typeof detail === 'string') return detail;
  } catch {}
  return raw || fallback;
}

export function KnowledgeBasePanel() {
  const { refreshCounter } = useDashboardStore();
  const { canManage, isPlatform } = useWorkspace();
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [status, setStatus] = useState<'idle' | 'success' | 'error'>('idle');
  const [message, setMessage] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [kbStatus, setKbStatus] = useState<KbStatus | null>(null);
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [searchResult, setSearchResult] = useState<SearchResponse | null>(null);
  const [searchError, setSearchError] = useState('');
  const [reindexing, setReindexing] = useState(false);

  async function loadStatus() {
    try {
      const res = await authFetch(`${getApiBase()}/api/knowledge/status`);
      if (res.ok) setKbStatus(await res.json());
    } catch {}
  }

  async function handleSearch() {
    if (!query.trim()) return;
    setSearching(true);
    setSearchError('');
    try {
      const res = await authFetch(`${getApiBase()}/api/knowledge/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: query.trim(), top_k: 3 }),
      });
      if (!res.ok) throw new Error(`Search failed (HTTP ${res.status})`);
      setSearchResult(await res.json());
    } catch (e: any) {
      setSearchResult(null);
      setSearchError(e.message || 'Search failed.');
    } finally {
      setSearching(false);
    }
  }

  async function handleReindex() {
    setReindexing(true);
    try {
      await authFetch(`${getApiBase()}/api/knowledge/reindex`, { method: 'POST' });
      await Promise.all([loadDocuments(), loadStatus()]);
    } finally {
      setReindexing(false);
    }
  }

  async function loadDocuments() {
    try {
      const res = await authFetch(`${getApiBase()}/api/knowledge/documents`);
      if (res.ok) {
        const data = await res.json();
        setDocs(data.documents || []);
      }
    } catch {
      // Fallback
    }
  }

  useEffect(() => {
    loadDocuments();
    loadStatus();
  }, [refreshCounter]);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      setFile(e.target.files[0]);
      setStatus('idle');
      setMessage('');
    }
  };

  const handleUpload = async () => {
    if (!file) return;
    
    setUploading(true);
    setStatus('idle');
    try {
      await uploadKnowledgeDocument(file);
      setStatus('success');
      setMessage(`"${file.name}" was indexed.`);
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
      loadDocuments();
      loadStatus();
    } catch (err: any) {
      setStatus('error');
      setMessage(readableError(err, 'Failed to upload document.'));
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (doc: KnowledgeDoc) => {
    if (!window.confirm(`Delete "${doc.name}"? Agents will stop answering from it and this cannot be undone.`)) return;
    try {
      const res = await authFetch(`${getApiBase()}/api/knowledge/documents/${encodeURIComponent(doc.id)}`, { method: 'DELETE' });
      if (!res.ok) {
        setStatus('error');
        setMessage(res.status === 404 ? 'That document no longer exists.' : `Delete failed (HTTP ${res.status}).`);
      }
      loadDocuments();
      loadStatus();
    } catch (e) {
      setStatus('error');
      setMessage('Could not reach the server to delete this document.');
    }
  };

  return (
    <Page
      width="max-w-4xl"
      title="Knowledge"
      subtitle="Documents your agents answer from. Upload manuals, policies and FAQs; callers get answers grounded in them."
    >
      {canManage && (
        <Card title="Add a document">
          <input type="file" className="hidden" ref={fileInputRef} onChange={handleFileChange} accept=".pdf,.docx,.md,.txt,.html,.htm,.csv" />
          {!file ? (
            <button
              onClick={() => fileInputRef.current?.click()}
              className="w-full rounded-lg border-2 border-dashed border-stone-300 dark:border-white/15 px-6 py-8 text-center hover:bg-stone-50 dark:hover:bg-white/5 transition-colors cursor-pointer"
            >
              <UploadCloud className="w-7 h-7 mx-auto text-stone-400" strokeWidth={1.5} />
              <p className="mt-2 text-[13px] font-medium text-stone-800 dark:text-stone-100">Choose a file to add</p>
              <p className="mt-0.5 text-[12px] text-stone-500 dark:text-stone-400">PDF, Word, text, Markdown, HTML or CSV. It is split into sections and indexed for search.</p>
            </button>
          ) : (
            <div className="flex items-center justify-between gap-3 rounded-lg border border-stone-200 dark:border-white/10 bg-stone-50 dark:bg-white/5 px-4 py-3">
              <div className="flex items-center gap-3 min-w-0">
                <FileText className="w-5 h-5 text-stone-500 flex-shrink-0" strokeWidth={1.5} />
                <div className="min-w-0">
                  <p className="text-[13px] font-medium text-stone-900 dark:text-white truncate">{file.name}</p>
                  <p className="text-[12px] text-stone-500">{(file.size / 1024).toFixed(1)} KB</p>
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <Button variant="ghost" onClick={() => { setFile(null); if (fileInputRef.current) fileInputRef.current.value = ''; }}>Cancel</Button>
                <Button variant="primary" onClick={handleUpload} disabled={uploading}>{uploading ? 'Indexing…' : 'Upload and index'}</Button>
              </div>
            </div>
          )}
          {status === 'success' && (
            <p className="mt-3 flex items-center gap-2 text-[13px] text-emerald-700 dark:text-emerald-400"><CheckCircle2 className="w-4 h-4" /> {message}</p>
          )}
          {status === 'error' && (
            <p className="mt-3 flex items-center gap-2 text-[13px] text-rose-700 dark:text-rose-400"><AlertCircle className="w-4 h-4" /> {message}</p>
          )}
        </Card>
      )}

      <Card title={`Documents (${docs.length})`} padded={false}>
        {docs.length === 0 ? (
          <EmptyState
            icon={<Database className="w-5 h-5" strokeWidth={1.75} />}
            title="No documents yet"
            text={canManage ? 'Add your first document above and your agents can start answering from it.' : 'Your administrator has not added any documents yet.'}
          />
        ) : (
          <ul className="divide-y divide-stone-100 dark:divide-white/5 mt-3">
            {docs.map((doc) => (
              <li key={doc.id} className="flex items-center gap-3 px-5 py-3">
                <span className="w-9 h-9 rounded-lg bg-stone-100 dark:bg-white/5 flex items-center justify-center text-stone-500 flex-shrink-0"><FileText className="w-4 h-4" strokeWidth={1.75} /></span>
                <div className="min-w-0 flex-1">
                  <p className="text-[13px] font-medium text-stone-900 dark:text-white truncate">{doc.name}</p>
                  <p className="text-[12px] text-stone-500 dark:text-stone-400">{docDetails(doc)}</p>
                </div>
                {doc.semantic
                  ? <Badge tone="green"><Sparkles className="w-3 h-3" /> Smart search</Badge>
                  : <span title="Found by matching words only. Smart search needs a vector database."><Badge tone="amber">Keyword only</Badge></span>}
                {canManage && (
                  <button
                    onClick={() => handleDelete(doc)}
                    className="p-2 rounded-lg text-stone-400 hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-500/10 cursor-pointer"
                    title="Delete document"
                    aria-label={`Delete ${doc.name}`}
                  >
                    <Trash2 className="w-4 h-4" strokeWidth={1.75} />
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Try a question">
        <div className="flex gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleSearch(); }}
            placeholder="Ask what a caller might ask, for example: how long is the warranty?"
            aria-label="Test question"
            className="flex-1 h-9 px-3 rounded-lg border border-stone-300 dark:border-white/15 bg-white dark:bg-white/5 text-[13px] text-stone-900 dark:text-white placeholder:text-stone-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          />
          <Button variant="primary" onClick={handleSearch} disabled={searching || !query.trim()}><Search className="w-4 h-4" /> {searching ? 'Searching…' : 'Search'}</Button>
        </div>
        {searchError && <p className="mt-3 text-[13px] text-rose-700 dark:text-rose-400">{searchError}</p>}
        {searchResult && (
          <div className="mt-4 space-y-3">
            <p className="text-[12px] text-stone-500 dark:text-stone-400">
              {searchResult.mode === 'skipped'
                ? 'Too short or conversational to look up, so live calls skip retrieval for this.'
                : searchResult.results.length === 0
                  ? 'No matching passages.'
                  : `${searchResult.results.length} matching passage${searchResult.results.length === 1 ? '' : 's'} in ${searchResult.timings_ms.total} ms (${MODE_LABEL[searchResult.mode] || `${searchResult.mode} search`})`}
            </p>
            {searchResult.results.map((r, i) => (
              <div key={`${r.doc_id}-${i}`} className="rounded-lg border border-stone-200 dark:border-white/10 bg-stone-50 dark:bg-white/5 p-3.5">
                <p className="text-[13px] font-medium text-stone-900 dark:text-white">{r.title}{r.section ? <span className="font-normal text-stone-500"> · {r.section}</span> : null}</p>
                <p className="text-[11px] text-stone-400 mt-0.5">{r.filename}</p>
                <p className="mt-2 text-[13px] text-stone-600 dark:text-stone-300 whitespace-pre-line leading-relaxed">{r.text}</p>
              </div>
            ))}
          </div>
        )}
        {kbStatus && (
          <div className="mt-5 pt-4 border-t border-stone-100 dark:border-white/5 flex items-center justify-between gap-3 text-[12px] text-stone-500 dark:text-stone-400">
            <p className="min-w-0 break-words">
              {kbStatus.vector.enabled
                ? <><span className="font-medium text-emerald-700 dark:text-emerald-400">Smart search on</span> · {kbStatus.semantic_documents} of {kbStatus.documents} documents indexed{kbStatus.vector.last_error ? <span className="text-amber-700 dark:text-amber-400"> · last issue: {kbStatus.vector.last_error}</span> : null}</>
                : <><span className="font-medium text-amber-700 dark:text-amber-400">Keyword search only</span> · smart search needs a vector database and an embedding key.</>}
            </p>
            {kbStatus.vector.enabled && isPlatform && (       // re-indexing rebuilds the platform's index: staff only
              <Button onClick={handleReindex} disabled={reindexing}><RefreshCw className={`w-3.5 h-3.5 ${reindexing ? 'animate-spin' : ''}`} /> Re-index</Button>
            )}
          </div>
        )}
      </Card>
    </Page>
  );
}
