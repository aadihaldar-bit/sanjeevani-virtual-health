"use client";

import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { auth, googleProvider } from "./firebase";
import { onAuthStateChanged, signInWithPopup, signInWithRedirect, getRedirectResult, signOut, User as FirebaseUser } from "firebase/auth";

export interface AuthUser {
  uid: string;
  email: string;
  displayName: string;
  photoURL?: string;
  initials: string;
}

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  signIn: () => Promise<void>;
  logOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  signIn: async () => {},
  logOut: async () => {}
});

function computeInitials(name?: string | null, email?: string | null): string {
  if (name && name.trim()) {
    const parts = name.trim().split(/\s+/);
    if (parts.length > 1) {
      return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
    }
    return (parts[0][0] || "U").toUpperCase();
  }
  if (email && email.trim()) {
    return (email[0] || "U").toUpperCase();
  }
  return "U";
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  const MOCK_USER: AuthUser = {
    uid: "local-dev-user-001",
    email: "admin@stltech.in",
    displayName: "Enterprise Admin",
    initials: "EA"
  };

  useEffect(() => {
    // 1. Immediately hydrate from localStorage if available
    if (typeof window !== "undefined") {
      const cached = localStorage.getItem("stl_auth_user");
      if (cached) {
        try {
          setUser(JSON.parse(cached));
          setLoading(false);
        } catch (e) {
          // ignore parsing error
        }
      }
    }

    const disableAuth = process.env.NEXT_PUBLIC_DISABLE_AUTH === "true";

    if (disableAuth || !auth) {
      setUser(MOCK_USER);
      if (typeof window !== "undefined") {
        localStorage.setItem("stl_auth_user", JSON.stringify(MOCK_USER));
      }
      setLoading(false);
      return;
    }

    // Check for redirect result if returning from signInWithRedirect
    if (auth) {
      getRedirectResult(auth)
        .then(async (result) => {
          if (result?.user) {
            const displayName = result.user.displayName || result.user.email?.split("@")[0] || "Google User";
            const email = result.user.email || "";
            const photoURL = result.user.photoURL || undefined;
            const initials = computeInitials(displayName, email);

            const authUser: AuthUser = {
              uid: result.user.uid,
              email,
              displayName,
              photoURL,
              initials
            };

            setUser(authUser);
            const token = await result.user.getIdToken();
            if (typeof window !== "undefined") {
              localStorage.setItem("stl_auth_token", token);
              localStorage.setItem("stl_auth_user", JSON.stringify(authUser));
            }
          }
        })
        .catch((err) => {
          console.warn("Firebase redirect auth result notice:", err);
        });
    }

    const unsubscribe = onAuthStateChanged(auth, async (firebaseUser: FirebaseUser | null) => {
      if (firebaseUser) {
        const displayName = firebaseUser.displayName || firebaseUser.email?.split("@")[0] || "Google User";
        const email = firebaseUser.email || "";
        const photoURL = firebaseUser.photoURL || undefined;
        const initials = computeInitials(displayName, email);

        const authUser: AuthUser = {
          uid: firebaseUser.uid,
          email,
          displayName,
          photoURL,
          initials
        };

        setUser(authUser);
        try {
          const token = await firebaseUser.getIdToken();
          if (typeof window !== "undefined") {
            localStorage.setItem("stl_auth_token", token);
            localStorage.setItem("stl_auth_user", JSON.stringify(authUser));
          }
        } catch (e) {
          console.error("Error retrieving Firebase ID token:", e);
        }
      } else {
        if (disableAuth) {
          setUser(MOCK_USER);
        } else {
          setUser(null);
          if (typeof window !== "undefined") {
            localStorage.removeItem("stl_auth_token");
            localStorage.removeItem("stl_auth_user");
          }
        }
      }
      setLoading(false);
    });

    return () => unsubscribe();
  }, []);

  const signIn = async () => {
    if (!auth || !googleProvider) {
      console.error("Firebase Auth or GoogleAuthProvider is not initialized");
      return;
    }

    try {
      setLoading(true);
      const result = await signInWithPopup(auth, googleProvider);
      if (result?.user) {
        const displayName = result.user.displayName || result.user.email?.split("@")[0] || "Google User";
        const email = result.user.email || "";
        const photoURL = result.user.photoURL || undefined;
        const initials = computeInitials(displayName, email);

        const authUser: AuthUser = {
          uid: result.user.uid,
          email,
          displayName,
          photoURL,
          initials
        };

        setUser(authUser);
        const token = await result.user.getIdToken();
        if (typeof window !== "undefined") {
          localStorage.setItem("stl_auth_token", token);
          localStorage.setItem("stl_auth_user", JSON.stringify(authUser));
        }
      }
    } catch (error: any) {
      console.warn("Google sign-in popup error, attempting redirect flow:", error);
      if (
        error?.code === "auth/popup-blocked" ||
        error?.code === "auth/popup-closed-by-user" ||
        error?.code === "auth/cancelled-popup-request" ||
        error?.message?.includes("Cross-Origin-Opener-Policy")
      ) {
        try {
          await signInWithRedirect(auth, googleProvider);
          return;
        } catch (redirectErr) {
          console.error("Redirect sign-in error:", redirectErr);
        }
      }
      if (error?.code === "auth/unauthorized-domain") {
        alert("Domain unauthorized: Please add 'voicebot-agent.stldigitaltech.com' in Firebase Console -> Authentication -> Settings -> Authorized Domains.");
      } else {
        alert(error?.message || "Google sign-in failed. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  };

  const logOut = async () => {
    try {
      if (auth) {
        await signOut(auth);
      }
    } catch (error) {
      console.error("Sign-out error:", error);
    } finally {
      setUser(null);
      if (typeof window !== "undefined") {
        localStorage.removeItem("stl_auth_token");
        localStorage.removeItem("stl_auth_user");
      }
    }
  };

  return (
    <AuthContext.Provider value={{ user, loading, signIn, logOut }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}

export async function getIdToken(): Promise<string | null> {
  if (auth?.currentUser) {
    try {
      return await auth.currentUser.getIdToken();
    } catch (e) {
      console.error("Error retrieving currentUser token:", e);
    }
  }
  if (typeof window !== "undefined") {
    return localStorage.getItem("stl_auth_token");
  }
  return null;
}
