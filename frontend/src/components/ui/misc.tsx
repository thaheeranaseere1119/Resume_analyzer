"use client";
import * as React from "react";
import * as P from "@radix-ui/react-progress";
import * as S from "@radix-ui/react-switch";
import * as C from "@radix-ui/react-checkbox";
import * as Tip from "@radix-ui/react-tooltip";
import { AlertTriangle, Check, Info, CheckCircle2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

export function Progress({ value, className, label }: { value: number; className?: string; label?: string }) {
  return (
    <P.Root value={value} aria-label={label} className={cn("relative h-2 w-full overflow-hidden rounded-full bg-muted", className)}>
      <P.Indicator className="h-full bg-primary transition-transform" style={{ transform: `translateX(-${100 - Math.max(0, Math.min(100, value))}%)` }} />
    </P.Root>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-pulse rounded-md bg-muted", className)} />;
}

export function Switch(p: React.ComponentProps<typeof S.Root>) {
  return (
    <S.Root {...p} className={cn("relative h-5 w-9 rounded-full bg-input data-[state=checked]:bg-primary", p.className)}>
      <S.Thumb className="block size-4 translate-x-0.5 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-[18px]" />
    </S.Root>
  );
}

export function Checkbox(p: React.ComponentProps<typeof C.Root>) {
  return (
    <C.Root {...p} className={cn("flex size-4 items-center justify-center rounded border border-input bg-card data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground", p.className)}>
      <C.Indicator><Check className="size-3" /></C.Indicator>
    </C.Root>
  );
}

export function Tooltip({ content, children }: { content: React.ReactNode; children: React.ReactNode }) {
  return (
    <Tip.Provider delayDuration={200}>
      <Tip.Root>
        <Tip.Trigger asChild>{children}</Tip.Trigger>
        <Tip.Portal>
          <Tip.Content sideOffset={6} className="z-50 max-w-xs rounded-md bg-foreground px-2.5 py-1.5 text-xs text-background shadow">{content}</Tip.Content>
        </Tip.Portal>
      </Tip.Root>
    </Tip.Provider>
  );
}

const alertStyles = {
  info: { cls: "bg-info-soft text-info border-info/20", Icon: Info },
  warning: { cls: "bg-warning-soft text-warning border-warning/20", Icon: AlertTriangle },
  danger: { cls: "bg-danger-soft text-danger border-danger/20", Icon: XCircle },
  success: { cls: "bg-success-soft text-success border-success/20", Icon: CheckCircle2 },
};

export function Alert({ variant = "info", title, children, className }: { variant?: keyof typeof alertStyles; title?: string; children?: React.ReactNode; className?: string }) {
  const { cls, Icon } = alertStyles[variant];
  return (
    <div role={variant === "danger" ? "alert" : "status"} className={cn("flex gap-3 rounded-lg border p-3 text-sm", cls, className)}>
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="min-w-0 space-y-1">{title && <p className="font-medium">{title}</p>}{children && <div className="text-foreground/80">{children}</div>}</div>
    </div>
  );
}

export function EmptyState({ icon, title, description, action }: { icon?: React.ReactNode; title: string; description?: string; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed p-10 text-center">
      {icon && <div className="text-muted-foreground [&_svg]:size-8">{icon}</div>}
      <div><p className="font-medium">{title}</p>{description && <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p>}</div>
      {action}
    </div>
  );
}

export function Separator({ className }: { className?: string }) {
  return <hr className={cn("border-border", className)} />;
}
