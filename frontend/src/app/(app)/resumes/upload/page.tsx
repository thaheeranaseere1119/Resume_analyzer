"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { FileUp, Loader2 } from "lucide-react";
import { ApiError } from "@/lib/api";
import { api } from "@/lib/api";
import type { ResumeSummary, UploadInfo } from "@/types/api";
import { PageHeader } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, Progress } from "@/components/ui/misc";
import { cn } from "@/lib/utils";

const MAX_MB = 10;

function csrf() {
  const m = document.cookie.match(/(?:^|;\s*)ats_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

/** XHR is used (instead of fetch) so we can show real upload progress. */
function uploadWithProgress(file: File, title: string, onProgress: (p: number) => void): Promise<ResumeSummary> {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    fd.append("file", file);
    if (title.trim()) fd.append("title", title.trim());
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/v1/resumes/upload");
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-CSRF-Token", csrf());
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      let body: { error?: { code?: string; message?: string } } & Partial<ResumeSummary> = {};
      try { body = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as ResumeSummary);
      else reject(new ApiError(xhr.status, body.error?.code || "upload_failed", body.error?.message || "Upload failed."));
    };
    xhr.onerror = () => reject(new ApiError(0, "network", "Network error. Check your connection and try again."));
    xhr.send(fd);
  });
}

export default function UploadPage() {
  const router = useRouter();
  const [file, setFile] = React.useState<File | null>(null);
  const [title, setTitle] = React.useState("");
  const [progress, setProgress] = React.useState(0);
  const [stage, setStage] = React.useState<"idle" | "uploading" | "processing">("idle");
  const [error, setError] = React.useState<string | null>(null);
  const [drag, setDrag] = React.useState(false);

  function pick(f: File | undefined) {
    setError(null);
    if (!f) return;
    const ext = f.name.split(".").pop()?.toLowerCase();
    if (ext !== "pdf" && ext !== "docx") return setError("Only PDF and DOCX files are supported.");
    if (f.size > MAX_MB * 1024 * 1024) return setError(`The file is larger than ${MAX_MB} MB.`);
    setFile(f);
    if (!title) setTitle(f.name.replace(/\.(pdf|docx)$/i, ""));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setError(null); setStage("uploading"); setProgress(0);
    try {
      const r = await uploadWithProgress(file, title, setProgress);
      setStage("processing");
      for (let i = 0; i < 120; i++) {
        const s = await api<UploadInfo>(`/resumes/${r.id}/status`);
        if (s.status === "ready") return router.push(`/resumes/${r.id}`);
        if (s.status === "failed") throw new ApiError(422, "parse_failed", s.error || "The document could not be processed.");
        await new Promise((res) => setTimeout(res, 1000));
      }
      throw new ApiError(504, "timeout", "Processing is taking longer than expected. Check the resume library in a minute.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
      setStage("idle");
    }
  }

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader title="Upload a resume" description="PDF or DOCX, up to 10 MB. Scanned PDFs are read with OCR and flagged for review." />
      <Card><CardContent className="p-6">
        <form onSubmit={submit} className="space-y-5">
          {error && <Alert variant="danger">{error}</Alert>}
          <label onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files[0]); }}
            className={cn("flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed p-8 text-center transition-colors",
              drag ? "border-primary bg-accent" : "border-input hover:bg-accent/50")}>
            <FileUp className="size-8 text-primary" aria-hidden />
            <span className="font-medium">{file ? file.name : "Drop your resume here or click to choose"}</span>
            <span className="text-xs text-muted-foreground">{file ? `${(file.size / 1024).toFixed(0)} KB` : "PDF or DOCX"}</span>
            <input type="file" accept=".pdf,.docx" className="sr-only" onChange={(e) => pick(e.target.files?.[0])} />
          </label>
          <div className="space-y-1.5"><Label htmlFor="title">Title</Label><Input id="title" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Software Engineer resume" /></div>
          {stage !== "idle" && (
            <div className="space-y-2" aria-live="polite">
              <Progress value={stage === "uploading" ? progress : 100} label="Upload progress" />
              <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />
                {stage === "uploading" ? `Uploading… ${progress}%` : "Extracting text and detecting sections…"}</p>
            </div>
          )}
          <Button type="submit" disabled={!file || stage !== "idle"}>Upload and parse</Button>
        </form>
      </CardContent></Card>
    </div>
  );
}
