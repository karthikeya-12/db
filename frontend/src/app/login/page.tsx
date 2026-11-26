import { Suspense } from "react";
import { AuthShell } from "@/components/auth-shell";
import { LoginForm } from "@/components/login-form";

export default function LoginPage() {
  return (
    <AuthShell eyebrow="Members only" title="Sign in" subtitle="Enter your details to open the library.">
      <Suspense>
        <LoginForm mode="login" />
      </Suspense>
    </AuthShell>
  );
}
