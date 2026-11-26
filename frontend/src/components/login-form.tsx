"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";

/** Only follow same-site relative paths, never `//host` or `https://...`. */
function safeNext(value: string | null): string {
  return value && value.startsWith("/") && !value.startsWith("//") ? value : "/data";
}

const labelClass = "text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground";

interface AuthConfig {
  signup_enabled: boolean;
  invite_required: boolean;
}

export function LoginForm({ mode }: { mode: "login" | "signup" }) {
  const signup = mode === "signup";
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [invite, setInvite] = useState("");
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<AuthConfig>("/api/auth/config").then(setConfig).catch(() => undefined);
  }, []);

  // Already signed in (valid cookie)? Skip the form.
  useEffect(() => {
    api
      .get("/api/auth/me")
      .then(() => router.replace(next))
      .catch(() => undefined);
  }, [router, next]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post(signup ? "/api/auth/signup" : "/api/auth/login", signup ? { email, password, invite_code: invite } : { email, password });
      router.replace(next);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not reach the service. Is the API running?");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-8 space-y-4">
      <div className="space-y-1.5">
        <label htmlFor="email" className={labelClass}>
          Email
        </label>
        <Input
          id="email"
          type="email"
          autoComplete="email"
          required
          autoFocus
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@company.com"
        />
      </div>
      <div className="space-y-1.5">
        <label htmlFor="password" className={labelClass}>
          Password
        </label>
        <Input
          id="password"
          type="password"
          autoComplete={signup ? "new-password" : "current-password"}
          minLength={signup ? 10 : undefined}
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>
      {signup && config?.invite_required && (
        <div className="space-y-1.5">
          <label htmlFor="invite" className={labelClass}>
            Invite code
          </label>
          <Input id="invite" required value={invite} onChange={(e) => setInvite(e.target.value)} autoComplete="off" />
        </div>
      )}
      {signup && <p className="text-xs text-muted-foreground">At least 10 characters.</p>}
      {signup && config && !config.signup_enabled && (
        <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          Sign-up is turned off. Ask an administrator for an account.
        </p>
      )}
      {error && (
        <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}
      <Button type="submit" disabled={busy} className="mt-2 w-full">
        {busy && <Loader2 className="animate-spin" />}
        {signup ? "Create account" : "Enter the library"}
      </Button>
      {signup ? (
        <p className="pt-2 text-center text-sm text-muted-foreground">
          Already a member?{" "}
          <Link href="/login" className="font-medium text-primary underline-offset-4 hover:underline">
            Sign in
          </Link>
        </p>
      ) : (
        config?.signup_enabled && (
          <p className="pt-2 text-center text-sm text-muted-foreground">
            New here?{" "}
            <Link href="/signup" className="font-medium text-primary underline-offset-4 hover:underline">
              Create an account
            </Link>
          </p>
        )
      )}
    </form>
  );
}
