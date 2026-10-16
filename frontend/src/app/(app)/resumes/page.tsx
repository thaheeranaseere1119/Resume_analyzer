"use client";
import * as React from "react";
import Link from "next/link";
import { FileText, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import type { ResumeSummary } from "@/types/api";
import { PageHeader } from "@/components/layout/app-shell";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, EmptyState, Skeleton } from "@/components/ui/misc";
import { ConfirmDialog } from "@/components/ui/dialog";

export default function ResumeLibrary() {
  const [items, setItems] = React.useState<ResumeSummary[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [toDelete, setToDelete] = React.useState<ResumeSummary | null>(null);
  const [deleting, setDeleting] = React.useState(false);

  const load = React.useCallback(() => {
    setError(null);
    api<ResumeSummary[]>("/resumes").then(setItems).catch((e) => setError(errorMessage(e)));
  }, []);
  React.useEffect(load, [load]);

  async function remove() {
    if (!toDelete) return;
    setDeleting(true);
    try { await api(`/resumes/${toDelete.id}`, { method: "DELETE" }); toast.success("Resume deleted"); setToDelete(null); load(); }
    catch (e) { toast.error(errorMessage(e)); }
    finally { setDeleting(false); }
  }

  return (
    <div>
      <PageHeader title="Resume library" description="Each upload is stored as an immutable original. Approved edits and corrections create new versions."
        actions={<Link href="/resumes/upload" className={buttonVariants()}><Plus /> Upload resume</Link>} />
      {error && <Alert variant="danger" className="mb-4">{error} <Button size="sm" variant="outline" onClick={load}>Retry</Button></Alert>}
      {!items ? <div className="grid gap-3 sm:grid-cols-2"><Skeleton className="h-28" /><Skeleton className="h-28" /></div> :
        items.length === 0 ? (
          <EmptyState icon={<FileText />} title="No resumes yet" description="Upload a PDF or DOCX resume to get started."
            action={<Link href="/resumes/upload" className={buttonVariants()}><Plus /> Upload resume</Link>} />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {items.map((r) => (
              <Card key={r.id}><CardContent className="flex items-start justify-between gap-3 p-4">
                <Link href={`/resumes/${r.id}`} className="min-w-0 flex-1">
                  <p className="truncate font-medium hover:underline">{r.title}</p>
                  <p className="truncate text-xs text-muted-foreground">{r.original_filename} · updated {formatDate(r.updated_at)}</p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <Badge variant={r.status === "ready" ? "success" : r.status === "failed" ? "danger" : "muted"}>{r.status}</Badge>
                    {r.file_format && <Badge variant="outline">{r.file_format.toUpperCase()}</Badge>}
                    <Badge variant="muted">{r.version_count} version{r.version_count === 1 ? "" : "s"}</Badge>
                  </div>
                </Link>
                <Button variant="ghost" size="icon" aria-label={`Delete ${r.title}`} onClick={() => setToDelete(r)}><Trash2 /></Button>
              </CardContent></Card>
            ))}
          </div>
        )}
      <ConfirmDialog open={!!toDelete} onOpenChange={(o) => !o && setToDelete(null)} title="Delete this resume?" destructive loading={deleting}
        confirmLabel="Delete resume" onConfirm={remove}
        description={<>“{toDelete?.title}”, its original upload, every version and related analyses will be permanently deleted.</>} />
    </div>
  );
}
