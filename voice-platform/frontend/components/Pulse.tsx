'use client';

import React from 'react';

interface PulseProps {
  active?: boolean;
  size?: 'sm' | 'md' | 'lg';
}

export function Pulse({ active = false, size = 'md' }: PulseProps) {
  const sizeClasses = {
    sm: 'w-2 h-2',
    md: 'w-3 h-3',
    lg: 'w-4 h-4',
  };

  if (!active) {
    return (
      <div className={`${sizeClasses[size]} rounded-full bg-slate-600`} />
    );
  }

  return (
    <div className="relative inline-flex">
      {/* Outer pulsing ring */}
      <div
        className={`${sizeClasses[size]} absolute rounded-full bg-red-500 animate-pulse-scale`}
      />
      {/* Inner solid dot */}
      <div
        className={`${sizeClasses[size]} rounded-full bg-red-600 relative z-10`}
      />
    </div>
  );
}

interface SpeakingIndicatorProps {
  isSpeaking: boolean;
}

export function SpeakingIndicator({ isSpeaking }: SpeakingIndicatorProps) {
  return (
    <div className="flex items-center gap-2">
      <div className="flex gap-1">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className={`w-1 rounded-full transition-all ${
              isSpeaking
                ? 'h-4 bg-red-500 animate-pulse'
                : 'h-2 bg-slate-600'
            }`}
            style={{
              animationDelay: isSpeaking ? `${i * 0.15}s` : '0s',
            }}
          />
        ))}
      </div>
      {isSpeaking && (
        <span className="text-xs text-red-500 font-semibold ml-2">
          Bot Speaking
        </span>
      )}
    </div>
  );
}
