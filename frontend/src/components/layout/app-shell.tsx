"use client";
import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { LogOut, Menu, Moon, Sparkles, Sun, X } from "lucide-react";
import { useAuth } from "@/features/auth/auth-context";
import { NAV } from "./nav";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/misc";
import { cn } from "@/lib/utils";

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, loading, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const { resolvedTheme, setTheme } = useTheme();
  const [open, setOpen] = React.useState(false);

  React.useEffect(() => {
    if (!loading && !user) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    else if (!loading && user && !user.profile?.onboarding_completed && pathname !== "/onboarding") router.replace("/onboarding");
  }, [loading, user, pathname, router]);

  React.useEffect(() => setOpen(false), [pathname]);

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center p-8" aria-busy="true">
        <div className="w-full max-w-md space-y-3"><Skeleton className="h-8 w-40" /><Skeleton className="h-24" /><Skeleton className="h-24" /></div>
      </div>
    );
  }

  const groups = Array.from(new Set(NAV.map((n) => n.group)));
  const sidebar = (
    <nav aria-label="Main" className="flex h-full flex-col gap-6 p-4">
      <Link href="/dashboard" className="flex items-center gap-2 px-2 font-serif text-lg font-bold">
        <Sparkles className="size-5 text-primary" aria-hidden /> CareerCopilot
      </Link>
      {groups.map((g) => (
        <div key={g} className="space-y-1">
          <p className="px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{g}</p>
          {NAV.filter((n) => n.group === g).map((n) => {
            const active = pathname === n.href || pathname.startsWith(n.href + "/");
            return (
              <Link key={n.href} href={n.href} aria-current={active ? "page" : undefined}
                className={cn("flex items-center gap-2 rounded-md px-2 py-1.5 text-sm", active ? "bg-accent font-medium text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground")}>
                <n.icon className="size-4" aria-hidden /> {n.label}
              </Link>
            );
          })}
        </div>
      ))}
      <div className="mt-auto space-y-2 border-t pt-4">
        <p className="truncate px-2 text-sm" title={user.email}>{user.profile?.full_name || user.email}</p>
        <div className="flex gap-1">
          <Button variant="ghost" size="sm" onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")} aria-label="Toggle colour theme">
            {resolvedTheme === "dark" ? <Sun /> : <Moon />} Theme
          </Button>
          <Button variant="ghost" size="sm" onClick={() => void logout()}><LogOut /> Sign out</Button>
        </div>
      </div>
    </nav>
  );

  return (
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-card focus:px-3 focus:py-2">Skip to content</a>
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 border-r bg-card lg:block">{sidebar}</aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 bg-card shadow-xl">{sidebar}</aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-2 border-b bg-background/90 px-4 py-2 backdrop-blur lg:hidden">
          <Button variant="ghost" size="icon" onClick={() => setOpen((o) => !o)} aria-label="Open navigation">{open ? <X /> : <Menu />}</Button>
          <span className="font-serif font-bold">CareerCopilot</span>
        </header>
        <main id="main" className="mx-auto w-full max-w-6xl flex-1 p-4 sm:p-6 lg:p-8">{children}</main>
      </div>
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="font-serif text-2xl font-bold sm:text-3xl">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}
