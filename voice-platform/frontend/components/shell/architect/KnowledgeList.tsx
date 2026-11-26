'use client';

import React, { useState } from 'react';
import { FileText, Loader2, Pencil } from 'lucide-react';
import { authFetch } from '@/lib/authFetch';
import { getApiBase } from '@/lib/api';

export interface DocRow {
  id: string;
  name: string;
  sections?: number;
  size_kb?: number;
  read_by?: string[];
  built_in?: boolean;
}

const BTN_SMALL = 'h-7 px-2.5 rounded-md border border-stone-300 dark:border-white/15 text-[12px] font-medium text-stone-700 dark:text-stone-200 hover:bg-stone-100 dark:hover:bg-white/10 cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed';
const BTN_PRIMARY = 'h-7 px-2.5 rounded-md bg-indigo-600 text-white text-[12px] font-medium hover:bg-indigo-500 cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed';

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
const sizeOf = (kb: number) => (kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : kb < 0.1 ? 'under 0.1 KB' : `${kb.toFixed(kb < 10 ? 1 : 0)} KB`);
const shownName = (name: string) => name.replace(/\.md$/i, '');

async function failure(res: Response, fallback: string): Promise<string> {
  try {
    const detail = (await res.json())?.detail;
    return typeof detail === 'string' ? detail : fallback;
  } catch { return fallback; }
}

/** One document: its name, who reads it, and its text to read or change right here. */
export function DocumentRow({ doc }: { doc: DocRow }) {
  const [text, setText] = useState<string | null>(null);
  const [editable, setEditable] = useState(false);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState<'loading' | 'saving' | null>(null);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);

  async function load(): Promise<string | null> {
    if (text !== null) return text;
    setBusy('loading');
    setError('');
    try {
      const res = await authFetch(`${getApiBase()}/api/knowledge/documents/${encodeURIComponent(doc.id)}`);
      if (!res.ok) throw new Error(await failure(res, 'I could not open this document.'));
      const data = await res.json();
      setText(String(data.content ?? ''));
      setEditable(!!data.editable);
      return String(data.content ?? '');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'I could not open this document.');
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function toggleRead() {
    if (open) { setOpen(false); setEditing(false); return; }
    setSaved(false);
    if ((await load()) !== null) setOpen(true);
  }

  async function startEdit() {
    setSaved(false);
    const current = await load();
    if (current === null) return;
    setDraft(current);
    setOpen(true);
    setEditing(true);
  }

  async function save() {
    setBusy('saving');
    setError('');
    try {
      const res = await authFetch(`${getApiBase()}/api/knowledge/documents/${encodeURIComponent(doc.id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: draft }),
      });
      if (!res.ok) throw new Error(await failure(res, 'I could not save this document.'));
      setText(draft);
      setEditing(false);
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'I could not save this document.');
    } finally {
      setBusy(null);
    }
  }

  const meta = [
    doc.sections !== undefined ? plural(doc.sections, 'section') : '',
    doc.size_kb !== undefined && doc.size_kb > 0 ? sizeOf(doc.size_kb) : '',
    doc.built_in ? 'built in' : '',
  ].filter(Boolean).join(' · ');
  const readers = doc.read_by ?? [];

  return (
    <div className="rounded-md bg-stone-50 dark:bg-white/5 px-2.5 py-2">
      <div className="flex items-start gap-2">
        <FileText className="w-3.5 h-3.5 mt-0.5 text-stone-400 flex-shrink-0" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="text-[12.5px] font-medium text-stone-900 dark:text-white break-words">{shownName(doc.name)}</p>
          {(meta || doc.read_by) && (
            <p className="text-[11.5px] text-stone-500 dark:text-stone-400">
              {meta}{meta && doc.read_by ? ' · ' : ''}
              {doc.read_by ? (readers.length ? `named by ${readers.join(', ')}` : 'read by agents that use all your documents') : ''}
            </p>
          )}
        </div>
      </div>
      <div className="mt-1.5 flex items-center gap-1.5">
        <button type="button" onClick={() => void toggleRead()} disabled={busy !== null} className={BTN_SMALL} aria-expanded={open}>
          {busy === 'loading' ? <Loader2 className="w-3 h-3 animate-spin inline" aria-label="Opening" /> : open ? 'Hide' : 'Read'}
        </button>
        {!doc.built_in && !editing && (
          <button type="button" onClick={() => void startEdit()} disabled={busy !== null} className={`${BTN_SMALL} inline-flex items-center gap-1`}>
            <Pencil className="w-3 h-3" aria-hidden="true" /> Edit
          </button>
        )}
        {saved && <span role="status" className="text-[11.5px] text-emerald-700 dark:text-emerald-400">Saved. Agents answer from the new text from now on.</span>}
      </div>
      {error && <p role="alert" className="mt-1.5 text-[11.5px] text-rose-600 dark:text-rose-400">{error}</p>}
      {open && text !== null && !editing && (
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border border-stone-200 dark:border-white/10 bg-white dark:bg-black/20 p-2.5 font-sans text-[12px] leading-relaxed text-stone-700 dark:text-stone-200">{text}</pre>
      )}
      {editing && (
        <div className="mt-2">
          <label htmlFor={`edit-${doc.id}`} className="sr-only">Text of {shownName(doc.name)}</label>
          <textarea
            id={`edit-${doc.id}`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={14}
            maxLength={200000}
            disabled={busy === 'saving'}
            className="w-full resize-y rounded-lg border border-stone-300 dark:border-white/15 bg-white dark:bg-white/5 px-2.5 py-2 font-mono text-[12px] leading-relaxed text-stone-800 dark:text-stone-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
          <div className="mt-1.5 flex items-center gap-1.5">
            <button type="button" onClick={() => void save()} disabled={busy !== null || !draft.trim() || draft === text} className={BTN_PRIMARY}>
              {busy === 'saving' ? 'Saving…' : 'Save'}
            </button>
            <button type="button" onClick={() => { setEditing(false); setError(''); }} disabled={busy === 'saving'} className={BTN_SMALL}>Cancel</button>
            {!editable && <span className="text-[11.5px] text-stone-500">This is a built-in document.</span>}
          </div>
        </div>
      )}
    </div>
  );
}

/** The company's documents, each one to read and change. */
export function KnowledgeList({ documents, onOpenPage }: { documents: DocRow[]; onOpenPage?: () => void }) {
  return (
    <div className="rounded-xl border border-stone-200 dark:border-white/10 bg-stone-50 dark:bg-white/5 p-3">
      <p className="text-[11px] font-medium text-stone-400">Your knowledge · {plural(documents.length, 'document')}</p>
      {documents.length === 0 ? (
        <p className="mt-1.5 text-[12.5px] text-stone-600 dark:text-stone-300">Nothing here yet. Ask me to write a document, or upload files under Knowledge.</p>
      ) : (
        <div className="mt-2 space-y-1.5">{documents.map((d) => <DocumentRow key={d.id} doc={d} />)}</div>
      )}
      {onOpenPage && (
        <button type="button" onClick={onOpenPage} className="mt-2.5 text-[12px] font-medium text-indigo-700 dark:text-indigo-300 hover:underline cursor-pointer">
          Open the Knowledge page
        </button>
      )}
    </div>
  );
}
