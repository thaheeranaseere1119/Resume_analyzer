"use client";
import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AlertTriangle, Download, Pencil, Save, X } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/lib/api";
import { cn, formatDate } from "@/lib/utils";
import type { Block, DocPreview, ResumeDetail, Version, VersionSummary } from "@/types/api";
import { PageHeader } from "@/components/layout/app-shell";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, EmptyState, Skeleton } from "@/components/ui/misc";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DocumentPreview } from "@/features/resumes/doc-preview";

const SECTION_TYPES = ["contact", "summary", "experience", "internships", "projects", "skills", "technical_skills", "soft_skills",
  "education", "certifications", "achievements", "publications", "languages", "volunteering", "interests", "other"];
const KINDS: Block["kind"][] = ["paragraph", "bullet", "entry_header", "skills_line", "contact"];
const label = (s: string) => s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

function confidenceBadge(c: number) {
  if (c >= 0.95) return null;
  return <Badge variant={c >= 0.75 ? "warning" : "danger"} title="Parser confidence">{Math.round(c * 100)}% confidence</Badge>;
}

export default function ResumeReview() {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = React.useState<ResumeDetail | null>(null);
  const [versions, setVersions] = React.useState<VersionSummary[]>([]);
  const [preview, setPreview] = React.useState<DocPreview | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [editing, setEditing] = React.useState<Record<string, { text: string; kind: Block["kind"]; section_type: string }>>({});
  const [saving, setSaving] = React.useState(false);

  const load = React.useCallback(async () => {
    setError(null);
    try {
      const d = await api<ResumeDetail>(`/resumes/${id}`);
      setData(d);
      setVersions(await api<VersionSummary[]>(`/resumes/${id}/versions`));
    } catch (e) { setError(errorMessage(e)); }
  }, [id]);
  React.useEffect(() => { void load(); }, [load]);

  const v: Version | null | undefined = data?.current_version;
  const loadPreview = React.useCallback(() => {
    if (!v) return;
    setPreview(null);
    api<DocPreview>(`/resumes/${id}/versions/${v.id}/preview`).then(setPreview).catch((e) => toast.error(errorMessage(e)));
  }, [id, v]);

  async function saveCorrections() {
    if (!v) return;
    setSaving(true);
    try {
      const sectionOf = (b: Block) => v.sections.find((s) => s.id === b.section_id)?.section_type ?? "other";
      const blocks = Object.entries(editing).map(([stable_id, e]) => {
        const b = v.blocks.find((x) => x.stable_id === stable_id)!;
        return { stable_id, ...(e.text !== b.text ? { text: e.text } : {}), ...(e.kind !== b.kind ? { kind: e.kind } : {}),
          ...(e.section_type !== sectionOf(b) ? { section_type: e.section_type } : {}) };
      }).filter((c) => Object.keys(c).length > 1);
      if (!blocks.length) { setEditing({}); return; }
      await api(`/resumes/${id}/corrections`, { body: { expected_version_id: v.id, blocks } });
      toast.success("Corrections saved as a new version");
      setEditing({});
      await load();
    } catch (e) { toast.error(errorMessage(e)); }
    finally { setSaving(false); }
  }

  async function saveProfile(form: HTMLFormElement) {
    if (!v) return;
    const fd = new FormData(form);
    const body: Record<string, unknown> = { expected_version_id: v.id };
    for (const k of ["name", "email", "phone", "location"]) body[k] = String(fd.get(k) || "").trim() || null;
    body.links = String(fd.get("links") || "").split(/[\n,]/).map((s) => s.trim()).filter(Boolean).slice(0, 10);
    try { await api(`/resumes/${id}/profile`, { method: "PATCH", body }); toast.success("Contact details corrected"); await load(); }
    catch (e) { toast.error(errorMessage(e)); }
  }

  if (error) return <Alert variant="danger" title="Couldn't load this resume">{error}</Alert>;
  if (!data) return <div className="space-y-3"><Skeleton className="h-10 w-72" /><Skeleton className="h-64" /></div>;
  if (data.status === "failed" || !v) {
    return (
      <div>
        <PageHeader title={data.title} />
        <Alert variant={data.status === "failed" ? "danger" : "info"} title={data.status === "failed" ? "Processing failed" : "Still processing"}>
          {data.upload?.error || "Text extraction is in progress. Refresh in a moment."}
        </Alert>
      </div>
    );
  }

  const warnings = v.parse_warnings;
  const dirty = Object.keys(editing).length > 0;
  return (
    <div>
      <PageHeader title={data.title}
        description={`Version ${v.version_number} · ${v.label} · ${data.upload?.file_format?.toUpperCase()}${v.page_count ? ` · ${v.page_count} page(s)` : ""}`}
        actions={<>
          <a className={buttonVariants({ variant: "outline" })} href={`/api/v1/resumes/${id}/versions/${v.id}/document`}><Download /> Download this version</a>
          <a className={buttonVariants({ variant: "ghost" })} href={`/api/v1/resumes/${id}/original`}>Original upload</a>
        </>} />

      {warnings.length > 0 && (
        <div className="mb-4 space-y-2">{warnings.map((w, i) => (
          <Alert key={i} variant={w.severity === "error" ? "danger" : w.severity === "warning" ? "warning" : "info"}>{w.message}</Alert>))}</div>
      )}

      <Tabs defaultValue="content" onValueChange={(t) => t === "preview" && !preview && loadPreview()}>
        <TabsList>
          <TabsTrigger value="content">Extracted content</TabsTrigger>
          <TabsTrigger value="contact">Contact details</TabsTrigger>
          <TabsTrigger value="preview">Document preview</TabsTrigger>
          <TabsTrigger value="versions">Versions ({versions.length})</TabsTrigger>
        </TabsList>

        <TabsContent value="content">
          <Card className="mb-4"><CardContent className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm">
            <p className="text-muted-foreground">Check that each line is in the right section. Corrections create a new version; your original file is never changed.</p>
            {dirty && <div className="flex gap-2"><Button variant="outline" onClick={() => setEditing({})}><X /> Discard</Button>
              <Button onClick={saveCorrections} loading={saving}><Save /> Save {Object.keys(editing).length} correction(s)</Button></div>}
          </CardContent></Card>
          <div className="space-y-4">
            {v.sections.map((s) => {
              const blocks = v.blocks.filter((b) => b.section_id === s.id);
              return (
                <Card key={s.id}>
                  <CardHeader className="flex-row flex-wrap items-center gap-2">
                    <CardTitle>{label(s.section_type)}</CardTitle>
                    {s.heading && <span className="text-xs text-muted-foreground">heading: “{s.heading}”</span>}
                    {s.page !== null && <Badge variant="muted">page {s.page + 1}</Badge>}
                    {confidenceBadge(s.confidence)}
                    {!s.heading && s.section_type !== "contact" && <Badge variant="warning"><AlertTriangle /> no heading – inferred</Badge>}
                  </CardHeader>
                  <CardContent className="space-y-2">
                    {blocks.length === 0 && <p className="text-sm text-muted-foreground">Empty section.</p>}
                    {blocks.map((b) => {
                      const ed = editing[b.stable_id];
                      return (
                        <div key={b.stable_id} className={cn("rounded-lg border p-3", ed && "border-primary")}>
                          {ed ? (
                            <div className="space-y-2">
                              <Textarea value={ed.text} rows={Math.min(8, Math.ceil(ed.text.length / 90) + 1)} aria-label="Block text"
                                onChange={(e) => setEditing({ ...editing, [b.stable_id]: { ...ed, text: e.target.value } })} />
                              <div className="flex flex-wrap gap-2">
                                <NativeSelect className="w-auto" aria-label="Section" value={ed.section_type}
                                  onChange={(e) => setEditing({ ...editing, [b.stable_id]: { ...ed, section_type: e.target.value } })}>
                                  {SECTION_TYPES.map((t) => <option key={t} value={t}>{label(t)}</option>)}
                                </NativeSelect>
                                <NativeSelect className="w-auto" aria-label="Line type" value={ed.kind}
                                  onChange={(e) => setEditing({ ...editing, [b.stable_id]: { ...ed, kind: e.target.value as Block["kind"] } })}>
                                  {KINDS.map((k) => <option key={k} value={k}>{label(k)}</option>)}
                                </NativeSelect>
                                <Button size="sm" variant="ghost" onClick={() => { const n = { ...editing }; delete n[b.stable_id]; setEditing(n); }}>Cancel</Button>
                              </div>
                            </div>
                          ) : (
                            <div className="flex items-start gap-3">
                              <div className="min-w-0 flex-1">
                                <p className="whitespace-pre-wrap text-sm">{b.text}</p>
                                <div className="mt-1.5 flex flex-wrap gap-1.5">
                                  <Badge variant="outline">{label(b.kind)}</Badge>
                                  {b.user_corrected && <Badge variant="info">corrected by you</Badge>}
                                  {confidenceBadge(b.confidence)}
                                </div>
                              </div>
                              <Button size="icon" variant="ghost" aria-label="Correct this line"
                                onClick={() => setEditing({ ...editing, [b.stable_id]: { text: b.text, kind: b.kind, section_type: s.section_type } })}><Pencil /></Button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </TabsContent>

        <TabsContent value="contact">
          <Card>
            <CardHeader><CardTitle>Contact details</CardTitle><CardDescription>Extracted automatically. Fields that could not be found are left empty, never guessed.</CardDescription></CardHeader>
            <CardContent>
              <form key={v.id} className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); void saveProfile(e.currentTarget); }}>
                {(["name", "email", "phone", "location"] as const).map((k) => (
                  <div key={k} className="space-y-1.5"><Label htmlFor={k}>{label(k)}</Label>
                    <Input id={k} name={k} defaultValue={v.profile[k] ?? ""} placeholder={v.profile[k] ? undefined : "Not found in the document"} /></div>
                ))}
                <div className="space-y-1.5 sm:col-span-2"><Label htmlFor="links">Profile links (one per line)</Label>
                  <Textarea id="links" name="links" defaultValue={(v.profile.links ?? []).join("\n")} rows={3} /></div>
                <div><Button type="submit"><Save /> Save contact details</Button></div>
              </form>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="preview">
          {preview ? <DocumentPreview preview={preview} /> : <Skeleton className="h-[600px]" />}
        </TabsContent>

        <TabsContent value="versions">
          {versions.length === 0 ? <EmptyState title="No versions" /> : (
            <Card><CardContent className="divide-y p-0">
              {versions.map((x) => (
                <div key={x.id} className="flex flex-wrap items-center justify-between gap-2 p-4 text-sm">
                  <div><p className="font-medium">v{x.version_number} · {x.label} {x.id === v.id && <Badge className="ml-1">current</Badge>}</p>
                    <p className="text-xs text-muted-foreground">{label(x.source)} · {formatDate(x.created_at, { dateStyle: "medium", timeStyle: "short" })}</p></div>
                  <a className={buttonVariants({ variant: "outline", size: "sm" })} href={`/api/v1/resumes/${id}/versions/${x.id}/document`}><Download /> Download</a>
                </div>
              ))}
            </CardContent></Card>
          )}
          <p className="mt-3 text-xs text-muted-foreground">Comparing, undoing and restoring versions arrive with the resume editor. <Link className="underline" href="/resumes">Back to library</Link></p>
        </TabsContent>
      </Tabs>
    </div>
  );
}
