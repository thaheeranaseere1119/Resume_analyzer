import Link from "next/link";
import { BarChart3, CheckCircle2, FileCheck2, GitCompare, ShieldCheck, Sparkles } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";

const FEATURES = [
  { icon: BarChart3, title: "Explainable compatibility", text: "A documented, deterministic score with component breakdowns and evidence for every requirement." },
  { icon: FileCheck2, title: "Evidence, not keywords", text: "Distinguishes skills you demonstrated in projects and roles from skills you only listed." },
  { icon: Sparkles, title: "Approval-based edits", text: "Every suggested change shows the original, the proposal, its evidence and its estimated impact before you accept it." },
  { icon: ShieldCheck, title: "No invented experience", text: "New tools, metrics or responsibilities require your confirmation. Unsupported claims are never applied." },
  { icon: GitCompare, title: "Versions & diffs", text: "Your original upload is immutable. Each accepted change creates a version you can compare, undo or restore." },
  { icon: CheckCircle2, title: "Exports that keep your design", text: "Edits are applied to your own DOCX or PDF, or exported to an ATS-friendly template." },
];

export default function Landing() {
  return (
    <div className="min-h-screen">
      <header className="mx-auto flex max-w-6xl items-center justify-between p-4 sm:p-6">
        <span className="flex items-center gap-2 font-serif text-xl font-bold"><Sparkles className="size-5 text-primary" /> CareerCopilot</span>
        <nav className="flex gap-2">
          <Link href="/login" className={buttonVariants({ variant: "ghost" })}>Sign in</Link>
          <Link href="/register" className={buttonVariants()}>Get started</Link>
        </nav>
      </header>
      <main className="mx-auto max-w-6xl px-4 pb-16 sm:px-6">
        <section className="py-12 sm:py-20">
          <h1 className="max-w-3xl font-serif text-4xl font-bold leading-tight sm:text-5xl">See how your resume matches a job – and fix it <span className="text-primary">without inventing anything.</span></h1>
          <p className="mt-5 max-w-2xl text-lg text-muted-foreground">An AI-powered ATS resume analyzer that explains every score, ties each suggestion to evidence in your resume, and applies only the changes you approve to your real document.</p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/register" className={buttonVariants({ size: "lg" })}>Analyze my resume</Link>
            <Link href="/login" className={buttonVariants({ size: "lg", variant: "outline" })}>I have an account</Link>
          </div>
        </section>
        <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-label="Features">
          {FEATURES.map((f) => (
            <div key={f.title} className="rounded-xl border bg-card p-5">
              <f.icon className="mb-3 size-5 text-primary" aria-hidden />
              <h2 className="font-semibold">{f.title}</h2>
              <p className="mt-1 text-sm text-muted-foreground">{f.text}</p>
            </div>
          ))}
        </section>
        <p className="mt-12 text-xs text-muted-foreground">Compatibility scores are internal estimates based on a documented method. They are not the score of any employer&apos;s ATS and do not guarantee interviews or offers.</p>
      </main>
    </div>
  );
}
