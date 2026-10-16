"use client";
import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
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

const schema = z.object({
  full_name: z.string().trim().min(1, "Enter your name").max(200),
  email: z.string().email("Enter a valid email"),
  password: z.string().min(10, "At least 10 characters").regex(/[A-Za-z]/, "Include a letter").regex(/\d/, "Include a number").max(128),
  confirm: z.string(),
}).refine((v) => v.password === v.confirm, { path: ["confirm"], message: "Passwords do not match" });

export default function RegisterPage() {
  const { register: signUp } = useAuth();
  const router = useRouter();
  const [error, setError] = React.useState<string | null>(null);
  const hydrated = useHydrated();
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema) });

  const onSubmit = handleSubmit(async (v) => {
    setError(null);
    try { await signUp(v.email, v.password, v.full_name); router.replace("/onboarding"); }
    catch (e) { setError(errorMessage(e)); }
  });

  const field = (id: "full_name" | "email" | "password" | "confirm", label: string, type = "text", auto?: string) => (
    <div className="space-y-1.5"><Label htmlFor={id}>{label}</Label>
      <Input id={id} type={type} autoComplete={auto} aria-invalid={!!errors[id]} {...register(id)} />
      <FieldError message={errors[id]?.message} /></div>
  );

  return (
    <Card>
      <CardHeader><CardTitle className="text-xl">Create your account</CardTitle>
        <CardDescription>Your resumes are private to your account. You can delete everything at any time.</CardDescription></CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} method="post" action="#" className="space-y-4" noValidate>
          {error && <Alert variant="danger">{error}</Alert>}
          {field("full_name", "Full name", "text", "name")}
          {field("email", "Email", "email", "email")}
          {field("password", "Password", "password", "new-password")}
          {field("confirm", "Confirm password", "password", "new-password")}
          <Button type="submit" className="w-full" loading={isSubmitting} disabled={!hydrated}>Create account</Button>
          <p className="text-center text-sm text-muted-foreground">Already registered? <Link className="text-primary underline" href="/login">Sign in</Link></p>
        </form>
      </CardContent>
    </Card>
  );
}
