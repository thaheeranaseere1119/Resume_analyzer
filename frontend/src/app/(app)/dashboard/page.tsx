"use client";
import * as React from "react";
import Link from "next/link";
import { BarChart3, Briefcase, FileText, ListChecks, Mail, Send } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { formatDate, formatScore } from "@/lib/utils";
import { PageHeader } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, EmptyState, Skeleton } from "@/components/ui/misc";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useAuth } from "@/features/auth/auth-context";

interface Dashboard {
  counts: Record<"resumes" | "jobs" | "analyses" | "pending_recommendations" | "cover_letters" | "applications", number>;
  application_statuses: Record<string, number>;
  recent_analyses: { id: string; score: number | null; status: string; created_at: string; job_title: string; company: string | null; resume_title: string }[];
  recent_versions: { id: string; label: string; version_number: number; created_at: string; resume_id: string; resume_title: string }[];
}

const STATS = [
  { key: "resumes", label: "Resumes", icon: FileText },
  { key: "jobs", label: "Saved jobs", icon: Briefcase },
  { key: "analyses", label: "Analyses", icon: BarChart3 },
  { key: "pending_recommendations", label: "Pending recommendations", icon: ListChecks },
  { key: "cover_letters", label: "Cover letters", icon: Mail },
  { key: "applications", label: "Applications", icon: Send },
] as const;

export default function DashboardPage() {
  const { user } = useAuth();
  const [data, setData] = React.useState<Dashboard | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const load = React.useCallback(() => {
    setError(null);
    api<Dashboard>("/dashboard").then(setData).catch((e) => setError(errorMessage(e)));
  }, []);
  React.useEffect(load, [load]);

  return (
    <div>
      <PageHeader title={`Hello${user?.profile?.full_name ? `, ${user.profile.full_name.split(" ")[0]}` : ""}`}
        description="Your resumes, analyses and applications at a glance." />
      {error && <Alert variant="danger" title="Couldn't load your dashboard" className="mb-4">{error} <Button size="sm" variant="outline" className="ml-2" onClick={load}>Retry</Button></Alert>}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        {STATS.map((s) => (
          <Card key={s.key}><CardContent className="p-4">
            <s.icon className="mb-2 size-4 text-muted-foreground" aria-hidden />
            {data ? <p className="text-2xl font-semibold tabular-nums">{data.counts[s.key]}</p> : <Skeleton className="h-8 w-10" />}
            <p className="text-xs text-muted-foreground">{s.label}</p>
          </CardContent></Card>
        ))}
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Recent analyses</CardTitle></CardHeader>
          <CardContent>
            {!data ? <Skeleton className="h-24" /> : data.recent_analyses.length === 0 ? (
              <EmptyState icon={<BarChart3 />} title="No analyses yet" description="Upload a resume and compare it with a job description to see an explainable compatibility estimate." />
            ) : (
              <ul className="divide-y">{data.recent_analyses.map((a) => (
                <li key={a.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                  <div className="min-w-0"><p className="truncate font-medium">{a.job_title}{a.company ? ` · ${a.company}` : ""}</p>
                    <p className="text-xs text-muted-foreground">{a.resume_title} · {formatDate(a.created_at)}</p></div>
                  <Badge variant={a.status === "done" ? "default" : "muted"}>{a.status === "done" ? formatScore(a.score) : a.status}</Badge>
                </li>))}</ul>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Recent resume versions</CardTitle></CardHeader>
          <CardContent>
            {!data ? <Skeleton className="h-24" /> : data.recent_versions.length === 0 ? (
              <EmptyState icon={<FileText />} title="No resumes yet" description="Your uploaded resume is kept as an immutable original; every approved change creates a new version." />
            ) : (
              <ul className="divide-y">{data.recent_versions.map((v) => (
                <li key={v.id} className="flex items-center justify-between py-2 text-sm">
                  <span className="truncate">{v.resume_title} · v{v.version_number} – {v.label}</span>
                  <span className="text-xs text-muted-foreground">{formatDate(v.created_at)}</span>
                </li>))}</ul>
            )}
          </CardContent>
        </Card>
      </div>
      {data && Object.keys(data.application_statuses).length > 0 && (
        <Card className="mt-6"><CardHeader><CardTitle>Applications by status</CardTitle></CardHeader>
          <CardContent className="flex flex-wrap gap-2">{Object.entries(data.application_statuses).map(([s, n]) => <Badge key={s} variant="outline">{s}: {n}</Badge>)}</CardContent></Card>
      )}
      <p className="mt-6 text-xs text-muted-foreground">Compatibility scores are internal estimates from this application&apos;s documented method. They are not any employer&apos;s ATS score and do not predict interviews or offers. <Link href="/settings" className="underline">Privacy settings</Link></p>
    </div>
  );
}
