import Link from "next/link";
import { Sparkles } from "lucide-react";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 p-4">
      <Link href="/" className="flex items-center gap-2 font-serif text-xl font-bold"><Sparkles className="size-5 text-primary" /> CareerCopilot</Link>
      <main className="w-full max-w-md">{children}</main>
    </div>
  );
}
