"use client";
import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useAuth } from "@/features/auth/auth-context";
import { useHydrated } from "@/hooks/use-hydrated";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import { Alert } from "@/components/ui/misc";
import { errorMessage } from "@/lib/api";

const schema = z.object({ email: z.string().email("Enter a valid email"), password: z.string().min(1, "Enter your password") });

function LoginForm() {
  const { login } = useAuth();
  const router = useRouter();
  const next = useSearchParams().get("next");
  const [error, setError] = React.useState<string | null>(null);
  const hydrated = useHydrated();
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema) });

  const onSubmit = handleSubmit(async (v) => {
    setError(null);
    try {
      const u = await login(v.email, v.password);
      router.replace(u.profile?.onboarding_completed ? (next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard") : "/onboarding");
    } catch (e) { setError(errorMessage(e)); }
  });

  return (
    <Card>
      <CardHeader><CardTitle className="text-xl">Sign in</CardTitle><CardDescription>Welcome back.</CardDescription></CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} method="post" action="#" className="space-y-4" noValidate>
          {error && <Alert variant="danger">{error}</Alert>}
          <div className="space-y-1.5"><Label htmlFor="email">Email</Label>
            <Input id="email" type="email" autoComplete="email" aria-invalid={!!errors.email} {...register("email")} />
            <FieldError message={errors.email?.message} /></div>
          <div className="space-y-1.5"><Label htmlFor="password">Password</Label>
            <Input id="password" type="password" autoComplete="current-password" aria-invalid={!!errors.password} {...register("password")} />
            <FieldError message={errors.password?.message} /></div>
          <Button type="submit" className="w-full" loading={isSubmitting} disabled={!hydrated}>Sign in</Button>
          <p className="text-center text-sm text-muted-foreground">New here? <Link className="text-primary underline" href="/register">Create an account</Link></p>
        </form>
      </CardContent>
    </Card>
  );
}

export default function LoginPage() {
  return <React.Suspense><LoginForm /></React.Suspense>;
}
