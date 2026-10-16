"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Laptop, Trash2 } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import type { Profile, SessionInfo } from "@/types/api";
import { PageHeader } from "@/components/layout/app-shell";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, Switch } from "@/components/ui/misc";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { ProfileForm } from "@/features/auth/profile-form";
import { useAuth } from "@/features/auth/auth-context";

export default function SettingsPage() {
  const { user, setProfile } = useAuth();
  const router = useRouter();
  const [sessions, setSessions] = React.useState<SessionInfo[] | null>(null);
  const [pw, setPw] = React.useState({ current: "", next: "" });
  const [del, setDel] = React.useState({ open: false, password: "", confirm: "", loading: false });

  const loadSessions = React.useCallback(() => {
    api<SessionInfo[]>("/account/sessions").then(setSessions).catch((e) => toast.error(errorMessage(e)));
  }, []);
  React.useEffect(loadSessions, [loadSessions]);

  async function toggleConsent(v: boolean) {
    try { setProfile(await api<Profile>("/account/profile", { method: "PATCH", body: { llm_consent: v } })); toast.success(v ? "AI features enabled" : "AI processing turned off"); }
    catch (e) { toast.error(errorMessage(e)); }
  }
  async function changePassword(e: React.FormEvent) {
    e.preventDefault();
    try { await api("/account/password", { body: { current_password: pw.current, new_password: pw.next } }); setPw({ current: "", next: "" }); toast.success("Password changed. Other sessions were signed out."); loadSessions(); }
    catch (err) { toast.error(errorMessage(err)); }
  }
  async function revoke(id: string) {
    try { await api(`/account/sessions/${id}`, { method: "DELETE" }); loadSessions(); toast.success("Session signed out"); }
    catch (e) { toast.error(errorMessage(e)); }
  }
  async function deleteAccount() {
    setDel((d) => ({ ...d, loading: true }));
    try { await api("/account", { method: "DELETE", body: { password: del.password, confirm: del.confirm } }); toast.success("Your account and files were deleted."); router.replace("/"); }
    catch (e) { toast.error(errorMessage(e)); setDel((d) => ({ ...d, loading: false })); }
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Settings & privacy" description="Manage your profile, security and how your data is processed." />
      <Card><CardHeader><CardTitle>Profile & career preferences</CardTitle></CardHeader><CardContent><ProfileForm /></CardContent></Card>

      <Card>
        <CardHeader><CardTitle>AI processing</CardTitle>
          <CardDescription>When enabled, only the resume excerpts and job requirements needed for a feature are sent to Anthropic Claude. Uploaded resumes are never used for model training.</CardDescription></CardHeader>
        <CardContent className="flex items-center gap-3">
          <Switch id="llm" checked={!!user?.profile?.llm_consent} onCheckedChange={toggleConsent} />
          <Label htmlFor="llm">{user?.profile?.llm_consent ? `Enabled since ${formatDate(user.profile.llm_consent_at)}` : "Disabled – the built-in writer (not AI) is used"}</Label>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Password</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={changePassword} method="post" action="#" className="grid gap-3 sm:grid-cols-3 sm:items-end">
            <div className="space-y-1.5"><Label htmlFor="cur">Current password</Label><Input id="cur" type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></div>
            <div className="space-y-1.5"><Label htmlFor="new">New password</Label><Input id="new" type="password" autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} /></div>
            <Button type="submit" disabled={!pw.current || pw.next.length < 10}>Change password</Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Active sessions</CardTitle></CardHeader>
        <CardContent>
          <ul className="divide-y">{(sessions ?? []).map((s) => (
            <li key={s.id} className="flex items-center justify-between gap-3 py-2 text-sm">
              <div className="flex min-w-0 items-center gap-2"><Laptop className="size-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0"><p className="truncate">{s.user_agent || "Unknown device"}</p>
                  <p className="text-xs text-muted-foreground">Signed in {formatDate(s.created_at)} · last used {formatDate(s.last_used_at)}</p></div></div>
              {s.current ? <Badge>This device</Badge> : <Button size="sm" variant="outline" onClick={() => revoke(s.id)}>Sign out</Button>}
            </li>))}</ul>
        </CardContent>
      </Card>

      <Card className="border-danger/40">
        <CardHeader><CardTitle className="text-danger">Delete account</CardTitle>
          <CardDescription>Permanently deletes your account, resumes (including original uploads), versions, analyses, letters and tracker data. This cannot be undone.</CardDescription></CardHeader>
        <CardContent><Button variant="destructive" onClick={() => setDel({ ...del, open: true })}><Trash2 /> Delete my account</Button></CardContent>
      </Card>
      <ConfirmDialog open={del.open} onOpenChange={(o) => setDel({ ...del, open: o })} title="Delete your account?" destructive confirmLabel="Delete everything"
        loading={del.loading} onConfirm={deleteAccount}
        description={<div className="space-y-3">
          <Alert variant="danger">All of your data and stored files will be removed.</Alert>
          <div className="space-y-1.5"><Label htmlFor="delpw">Password</Label><Input id="delpw" type="password" value={del.password} onChange={(e) => setDel({ ...del, password: e.target.value })} /></div>
          <div className="space-y-1.5"><Label htmlFor="delc">Type DELETE to confirm</Label><Input id="delc" value={del.confirm} onChange={(e) => setDel({ ...del, confirm: e.target.value })} /></div>
        </div>} />
    </div>
  );
}
