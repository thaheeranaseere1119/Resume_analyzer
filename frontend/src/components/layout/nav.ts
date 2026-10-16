import { FileText, LayoutDashboard, Settings, type LucideIcon } from "lucide-react";

export interface NavItem { href: string; label: string; icon: LucideIcon; group: string }

// Only pages that are implemented are listed; new entries are added as each phase lands.
export const NAV: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, group: "Overview" },
  { href: "/resumes", label: "Resume library", icon: FileText, group: "Resumes" },
  { href: "/settings", label: "Settings & privacy", icon: Settings, group: "Account" },
];
