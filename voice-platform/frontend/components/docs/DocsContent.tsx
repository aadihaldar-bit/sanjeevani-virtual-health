'use client';

import React, { useState } from 'react';
import { EndpointDoc } from '@/lib/docsData';
import { Copy, Check, Shield, Zap, Sparkles, ChevronDown, ChevronRight, Layers, ArrowRight } from 'lucide-react';

interface DocsContentProps {
  endpoint: EndpointDoc;
  host: string;
}

export function DocsContent({ endpoint, host }: DocsContentProps) {
  const [copiedPath, setCopiedPath] = useState(false);
  const [selectedResponseIndex, setSelectedResponseIndex] = useState(0);
  const [copiedResponse, setCopiedResponse] = useState(false);

  const cleanHost = host.replace(/\/$/, '');
  const fullUrl = `${cleanHost}${endpoint.path}`;

  const copyPathToClipboard = () => {
    navigator.clipboard.writeText(fullUrl);
    setCopiedPath(true);
    setTimeout(() => setCopiedPath(false), 2000);
  };

  const copyResponseToClipboard = (example: any) => {
    const text = typeof example === 'string' ? example : JSON.stringify(example, null, 2);
    navigator.clipboard.writeText(text);
    setCopiedResponse(true);
    setTimeout(() => setCopiedResponse(false), 2000);
  };

  const getMethodBadge = (method: string) => {
    switch (method) {
      case 'GET':
        return 'text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-950/60 border-emerald-300 dark:border-emerald-800';
      case 'POST':
        return 'text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-950/60 border-blue-300 dark:border-blue-800';
      case 'WS':
        return 'text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/60 border-amber-300 dark:border-amber-800';
      case 'PATCH':
        return 'text-purple-700 dark:text-purple-300 bg-purple-50 dark:bg-purple-950/60 border-purple-300 dark:border-purple-800';
      case 'DELETE':
        return 'text-rose-700 dark:text-rose-300 bg-rose-50 dark:bg-rose-950/60 border-rose-300 dark:border-rose-800';
      default:
        return 'text-stone-700 dark:text-stone-300 bg-stone-100 dark:bg-stone-800 border-stone-300';
    }
  };

  const activeResponse = endpoint.responses[selectedResponseIndex] || endpoint.responses[0];

  return (
    <div className="max-w-3xl xl:max-w-2xl py-8 px-4 sm:px-8 space-y-10">
      {/* Breadcrumbs */}
      <div className="flex items-center gap-1.5 text-xs text-stone-500 dark:text-stone-400 font-medium">
        <span>API Reference</span>
        <ChevronRight className="w-3 h-3 text-stone-400" />
        <span className="text-stone-700 dark:text-stone-300">{endpoint.category}</span>
        <ChevronRight className="w-3 h-3 text-stone-400" />
        <span className="text-indigo-600 dark:text-indigo-400 font-semibold">{endpoint.title}</span>
      </div>

      {/* Header Section */}
      <div className="space-y-4">
        <h1 className="text-2xl sm:text-3xl font-extrabold tracking-tight text-stone-900 dark:text-white font-sans">
          {endpoint.title}
        </h1>

        {/* Method & Path Banner */}
        <div className="flex flex-wrap items-center gap-2 p-2.5 rounded-xl bg-stone-100 dark:bg-[#11141b] border border-stone-200 dark:border-white/10 font-mono text-xs">
          <span className={`px-2 py-0.5 font-bold uppercase rounded border tracking-wide ${getMethodBadge(endpoint.method)}`}>
            {endpoint.method}
          </span>
          <span className="font-semibold text-stone-800 dark:text-stone-200 truncate">
            {endpoint.path}
          </span>
          <button
            onClick={copyPathToClipboard}
            title="Copy full URL"
            className="ml-auto inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-sans font-medium text-stone-500 hover:text-stone-900 dark:text-stone-400 dark:hover:text-white bg-white dark:bg-stone-800/80 border border-stone-200 dark:border-white/10 transition-colors"
          >
            {copiedPath ? (
              <>
                <Check className="w-3 h-3 text-emerald-500" />
                <span className="text-emerald-600 dark:text-emerald-400">Copied</span>
              </>
            ) : (
              <>
                <Copy className="w-3 h-3" />
                <span>Copy URL</span>
              </>
            )}
          </button>
        </div>

        {/* Badges / SLAs */}
        <div className="flex flex-wrap items-center gap-2 pt-1">
          {endpoint.latencySla && (
            <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-mono font-medium bg-indigo-50 dark:bg-indigo-950/40 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800/40">
              <Zap className="w-3 h-3 text-indigo-500" />
              <span>Target SLA: {endpoint.latencySla}</span>
            </div>
          )}
          {endpoint.productionReady && (
            <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-800/40">
              <Shield className="w-3 h-3 text-emerald-500" />
              <span>Production Ready (v2.5)</span>
            </div>
          )}
        </div>

        {/* Description */}
        <p className="text-sm sm:text-base text-stone-600 dark:text-stone-300 leading-relaxed font-sans pt-2">
          {endpoint.description}
        </p>
      </div>

      {/* Request Headers */}
      {endpoint.headers && endpoint.headers.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-bold uppercase tracking-wider text-stone-800 dark:text-stone-200 flex items-center gap-2">
            <Layers className="w-4 h-4 text-indigo-500" />
            <span>Headers</span>
          </h2>
          <div className="border border-stone-200 dark:border-white/10 rounded-xl overflow-hidden bg-white dark:bg-[#0f1118]">
            <table className="w-full text-left text-xs">
              <thead className="bg-stone-50 dark:bg-white/5 text-stone-500 dark:text-stone-400 uppercase font-mono text-[10px] border-b border-stone-200 dark:border-white/10">
                <tr>
                  <th className="px-4 py-2.5 font-semibold">Header</th>
                  <th className="px-4 py-2.5 font-semibold">Type</th>
                  <th className="px-4 py-2.5 font-semibold">Required</th>
                  <th className="px-4 py-2.5 font-semibold">Description</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100 dark:divide-white/5">
                {endpoint.headers.map((h) => (
                  <tr key={h.name} className="hover:bg-stone-50/50 dark:hover:bg-white/[0.02]">
                    <td className="px-4 py-3 font-mono font-medium text-stone-900 dark:text-white">
                      {h.name}
                    </td>
                    <td className="px-4 py-3 font-mono text-stone-500 dark:text-stone-400">
                      {h.type}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                          h.required
                            ? 'bg-rose-50 dark:bg-rose-950/40 text-rose-600 dark:text-rose-400'
                            : 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400'
                        }`}
                      >
                        {h.required ? 'required' : 'optional'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-stone-600 dark:text-stone-300">
                      {h.description}
                      {h.example && (
                        <span className="block mt-1 text-[11px] font-mono text-indigo-600 dark:text-indigo-400">
                          e.g. {h.example}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Query Parameters */}
      {endpoint.queryParams && endpoint.queryParams.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-bold uppercase tracking-wider text-stone-800 dark:text-stone-200">
            Query Parameters
          </h2>
          <div className="border border-stone-200 dark:border-white/10 rounded-xl overflow-hidden bg-white dark:bg-[#0f1118]">
            <table className="w-full text-left text-xs">
              <thead className="bg-stone-50 dark:bg-white/5 text-stone-500 dark:text-stone-400 uppercase font-mono text-[10px] border-b border-stone-200 dark:border-white/10">
                <tr>
                  <th className="px-4 py-2.5 font-semibold">Parameter</th>
                  <th className="px-4 py-2.5 font-semibold">Type</th>
                  <th className="px-4 py-2.5 font-semibold">Status</th>
                  <th className="px-4 py-2.5 font-semibold">Description</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100 dark:divide-white/5">
                {endpoint.queryParams.map((p) => (
                  <tr key={p.name} className="hover:bg-stone-50/50 dark:hover:bg-white/[0.02]">
                    <td className="px-4 py-3 font-mono font-medium text-stone-900 dark:text-white">
                      {p.name}
                    </td>
                    <td className="px-4 py-3 font-mono text-stone-500 dark:text-stone-400">
                      {p.type}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                          p.required
                            ? 'bg-rose-50 dark:bg-rose-950/40 text-rose-600 dark:text-rose-400'
                            : 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400'
                        }`}
                      >
                        {p.required ? 'required' : 'optional'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-stone-600 dark:text-stone-300">
                      <div>{p.description}</div>
                      {p.enum && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {p.enum.map((val) => (
                            <span
                              key={val}
                              className="px-1.5 py-0.2 rounded text-[10px] font-mono bg-stone-100 dark:bg-white/5 text-stone-600 dark:text-stone-300 border border-stone-200 dark:border-white/10"
                            >
                              {val}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Request Body Parameters */}
      {endpoint.bodyParams && endpoint.bodyParams.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-bold uppercase tracking-wider text-stone-800 dark:text-stone-200">
            Request Body Parameters
          </h2>
          <div className="border border-stone-200 dark:border-white/10 rounded-xl overflow-hidden bg-white dark:bg-[#0f1118]">
            <table className="w-full text-left text-xs">
              <thead className="bg-stone-50 dark:bg-white/5 text-stone-500 dark:text-stone-400 uppercase font-mono text-[10px] border-b border-stone-200 dark:border-white/10">
                <tr>
                  <th className="px-4 py-2.5 font-semibold">Field</th>
                  <th className="px-4 py-2.5 font-semibold">Type</th>
                  <th className="px-4 py-2.5 font-semibold">Status</th>
                  <th className="px-4 py-2.5 font-semibold">Description</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100 dark:divide-white/5">
                {endpoint.bodyParams.map((p) => (
                  <tr key={p.name} className="hover:bg-stone-50/50 dark:hover:bg-white/[0.02]">
                    <td className="px-4 py-3 font-mono font-medium text-stone-900 dark:text-white">
                      {p.name}
                    </td>
                    <td className="px-4 py-3 font-mono text-stone-500 dark:text-stone-400">
                      {p.type}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                          p.required
                            ? 'bg-rose-50 dark:bg-rose-950/40 text-rose-600 dark:text-rose-400'
                            : 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400'
                        }`}
                      >
                        {p.required ? 'required' : 'optional'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-stone-600 dark:text-stone-300">
                      <div>{p.description}</div>
                      {p.enum && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {p.enum.map((val) => (
                            <span
                              key={val}
                              className="px-1.5 py-0.2 rounded text-[10px] font-mono bg-stone-100 dark:bg-white/5 text-stone-600 dark:text-stone-300 border border-stone-200 dark:border-white/10"
                            >
                              {val}
                            </span>
                          ))}
                        </div>
                      )}
                      {p.default !== undefined && (
                        <div className="mt-1 text-[11px] text-stone-400 dark:text-stone-500 font-mono">
                          default: {String(p.default)}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Response Schema Section */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-bold uppercase tracking-wider text-stone-800 dark:text-stone-200">
            Responses
          </h2>
          <div className="flex items-center gap-1.5">
            {endpoint.responses.map((resp, idx) => (
              <button
                key={resp.status}
                onClick={() => setSelectedResponseIndex(idx)}
                className={`px-2.5 py-1 rounded-md text-xs font-mono font-medium transition-all ${
                  selectedResponseIndex === idx
                    ? 'bg-stone-900 text-white dark:bg-white dark:text-stone-900 shadow-2xs'
                    : 'bg-stone-100 dark:bg-stone-800/80 text-stone-600 dark:text-stone-400 hover:text-stone-900 dark:hover:text-white'
                }`}
              >
                {resp.status}
              </button>
            ))}
          </div>
        </div>

        <div className="border border-stone-200 dark:border-white/10 rounded-xl overflow-hidden bg-stone-950 text-stone-200 font-mono text-xs">
          <div className="flex items-center justify-between px-4 py-2 bg-stone-900 border-b border-stone-800 text-[11px] text-stone-400">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
              <span>{activeResponse.status} OK · {activeResponse.description}</span>
            </div>
            <button
              onClick={() => copyResponseToClipboard(activeResponse.example)}
              className="inline-flex items-center gap-1 text-[11px] hover:text-white transition-colors"
            >
              {copiedResponse ? (
                <>
                  <Check className="w-3 h-3 text-emerald-400" />
                  <span className="text-emerald-400">Copied</span>
                </>
              ) : (
                <>
                  <Copy className="w-3 h-3" />
                  <span>Copy</span>
                </>
              )}
            </button>
          </div>
          <div className="p-4 overflow-x-auto max-h-80 overscroll-contain">
            <pre className="text-xs leading-relaxed text-emerald-300/90 whitespace-pre">
              {typeof activeResponse.example === 'string'
                ? activeResponse.example
                : JSON.stringify(activeResponse.example, null, 2)}
            </pre>
          </div>
        </div>
      </section>
    </div>
  );
}
