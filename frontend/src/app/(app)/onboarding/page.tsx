"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { ShieldCheck } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/misc";
import { Label } from "@/components/ui/label";
import { ProfileForm } from "@/features/auth/profile-form";
import { PageHeader } from "@/components/layout/app-shell";

export default function OnboardingPage() {
  const router = useRouter();
  const [consent, setConsent] = React.useState(false);
  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="Welcome to CareerCopilot" description="Tell us what you're aiming for. You can change this any time in Settings." />
      <Card className="mb-6">
        <CardHeader><CardTitle className="flex items-center gap-2"><ShieldCheck className="size-4 text-primary" /> How your data is used</CardTitle></CardHeader>
        <CardContent className="space-y-3 text-sm text-muted-foreground">
          <p>Your resume and job descriptions are stored privately in your account. Parsing, keyword matching and scoring run on this server.</p>
          <p>AI writing features (rewording suggestions, cover letters, interview questions, roadmaps) send <strong>only the relevant resume excerpts and job requirements</strong> to the configured AI provider (Anthropic Claude). Nothing is used to train models. Without consent, a built-in rule-based writer is used instead and is clearly labelled.</p>
          <div className="flex items-start gap-2 pt-1">
            <Checkbox id="consent" checked={consent} onCheckedChange={(v) => setConsent(v === true)} />
            <Label htmlFor="consent" className="font-normal leading-snug text-foreground">I agree that relevant excerpts may be sent to the AI provider for the features I use.</Label>
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Career preferences</CardTitle><CardDescription>Used for job matching, interview preparation and roadmaps.</CardDescription></CardHeader>
        <CardContent><ProfileForm submitLabel="Continue" extra={{ onboarding_completed: true, llm_consent: consent }} onSaved={() => router.replace("/dashboard")} /></CardContent>
      </Card>
    </div>
  );
}
