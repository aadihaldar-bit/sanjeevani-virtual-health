"use client";

import { ReactNode } from "react";
import { AuthProvider, useAuth } from "@/lib/useAuth";

const ALLOWED_EMAILS = (process.env.NEXT_PUBLIC_ALLOWED_AUTH_EMAILS || "")
  .split(",")
  .map((e) => e.trim().toLowerCase())
  .filter(Boolean);

function GateInner({ children }: { children: ReactNode }) {
  const { user, loading, signIn, logOut } = useAuth();
  const disableAuth = process.env.NEXT_PUBLIC_DISABLE_AUTH === "true" || !process.env.NEXT_PUBLIC_FIREBASE_API_KEY;

  if (disableAuth) {
    return <>{children}</>;
  }

  if (loading) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-slate-50">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600" />
      </div>
    );
  }

  if (ALLOWED_EMAILS.length > 0 && user && !ALLOWED_EMAILS.includes((user.email || "").toLowerCase())) {
    return (
      <div className="flex h-screen w-screen flex-col items-center justify-center gap-4 bg-slate-50">
        <div className="text-center">
          <h1 className="text-xl font-semibold text-slate-900">Not authorized</h1>
          <p className="mt-2 text-sm text-slate-500">
            {user.email} does not have access to this dashboard.
          </p>
        </div>
        <button
          onClick={logOut}
          className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
        >
          Sign out
        </button>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="flex h-screen w-screen flex-col items-center justify-center gap-6 bg-slate-50">
        <div className="text-center">
          <h1 className="text-2xl font-semibold text-slate-900">STL Digital Voice Agent</h1>
          <p className="mt-2 text-sm text-slate-500">Sign in to access the dashboard</p>
        </div>
        <button
          onClick={signIn}
          className="flex items-center gap-3 rounded-lg border border-slate-300 bg-white px-5 py-2.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
        >
          <svg width="18" height="18" viewBox="0 0 48 48">
            <path fill="#FFC107" d="M43.611 20.083H42V20H24v8h11.303c-1.649 4.657-6.08 8-11.303 8-6.627 0-12-5.373-12-12s5.373-12 12-12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 12.955 4 4 12.955 4 24s8.955 20 20 20 20-8.955 20-20c0-1.341-.138-2.65-.389-3.917z" />
            <path fill="#FF3D00" d="M6.306 14.691l6.571 4.819C14.655 15.108 18.961 12 24 12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 16.318 4 9.656 8.337 6.306 14.691z" />
            <path fill="#4CAF50" d="M24 44c5.166 0 9.86-1.977 13.409-5.192l-6.19-5.238C29.211 35.091 26.715 36 24 36c-5.202 0-9.619-3.317-11.283-7.946l-6.522 5.025C9.505 39.556 16.227 44 24 44z" />
            <path fill="#1976D2" d="M43.611 20.083H42V20H24v8h11.303a12.04 12.04 0 0 1-4.087 5.571l6.19 5.238C41.202 35.833 44 30.333 44 24c0-1.341-.138-2.65-.389-3.917z" />
          </svg>
          Sign in with Google
        </button>
      </div>
    );
  }

  return <>{children}</>;
}

export default function AuthGate({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <GateInner>{children}</GateInner>
    </AuthProvider>
  );
}
