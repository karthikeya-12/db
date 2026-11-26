import { Suspense } from "react";
import { AuthShell } from "@/components/auth-shell";
import { LoginForm } from "@/components/login-form";

export default function SignupPage() {
  return (
    <AuthShell eyebrow="New member" title="Create account" subtitle="Choose an email and a password to join the library.">
      <Suspense>
        <LoginForm mode="signup" />
      </Suspense>
    </AuthShell>
  );
}
