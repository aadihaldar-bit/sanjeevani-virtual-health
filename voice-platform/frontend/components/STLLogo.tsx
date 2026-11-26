import React from 'react';

export function STLLogo({
  className = "h-6",
  dark = false,
  compact = true,
  showBadge = false,
}: {
  className?: string;
  dark?: boolean;
  compact?: boolean;
  showBadge?: boolean;
}) {
  return (
    <div className={`flex items-center gap-2 select-none ${className}`}>
      <div className="flex items-center tracking-tight">
        <span className={`font-black text-lg tracking-tighter font-sans leading-none ${dark ? 'text-white' : 'text-stone-900 dark:text-white'}`}>
          STL
        </span>
        <svg
          className={`w-3.5 h-3.5 mx-1 stroke-[2.5] flex-shrink-0 ${dark ? 'text-indigo-400' : 'text-indigo-600 dark:text-indigo-400'}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M9 5l7 7-7 7"
          />
        </svg>
        <span className={`font-light text-base tracking-normal font-sans leading-none ${dark ? 'text-stone-200' : 'text-stone-700 dark:text-stone-200'}`}>
          Digital
        </span>
      </div>
      {showBadge && !compact && (
        <>
          <div className={`h-3.5 w-[1px] ${dark ? 'bg-white/20' : 'bg-stone-300 dark:bg-white/20'}`} />
          <span className={`text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded border ${
            dark
              ? 'bg-indigo-950/70 text-indigo-300 border-indigo-500/30'
              : 'bg-indigo-50 text-indigo-700 border-indigo-200 dark:bg-indigo-950/70 dark:text-indigo-300 dark:border-indigo-800/40'
          }`}>
            Voice Agent
          </span>
        </>
      )}
    </div>
  );
}
