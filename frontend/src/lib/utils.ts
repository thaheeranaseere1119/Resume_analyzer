import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(value: string | null | undefined, opts: Intl.DateTimeFormatOptions = { dateStyle: "medium" }) {
  if (!value) return "–";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "–" : d.toLocaleString(undefined, opts);
}

export function formatScore(n: number | null | undefined) {
  return n === null || n === undefined ? "–" : n.toFixed(1);
}

export function signed(n: number | null | undefined, digits = 1) {
  if (n === null || n === undefined) return "–";
  const v = Number(n.toFixed(digits));
  return `${v > 0 ? "+" : v < 0 ? "−" : "±"}${Math.abs(v).toFixed(digits)}`;
}
