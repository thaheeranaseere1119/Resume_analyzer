"use client";
import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { api, errorMessage } from "@/lib/api";
import type { Profile } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect } from "@/components/ui/input";
import { FieldError, Label } from "@/components/ui/label";
import { useAuth } from "./auth-context";

const schema = z.object({
  full_name: z.string().trim().max(200),
  headline: z.string().trim().max(300),
  location: z.string().trim().max(200),
  target_roles: z.string().max(600),
  remote_preference: z.enum(["any", "remote", "hybrid", "onsite"]),
  experience_level: z.enum(["student", "entry", "mid", "senior"]),
  weekly_study_hours: z.coerce.number().int().min(1).max(60),
});
type Values = z.infer<typeof schema>;

export function ProfileForm({ submitLabel = "Save profile", extra, onSaved }: { submitLabel?: string; extra?: Partial<Profile>; onSaved?: (p: Profile) => void }) {
  const { user, setProfile } = useAuth();
  const p = user?.profile;
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<Values>({
    resolver: zodResolver(schema) as never,
    defaultValues: {
      full_name: p?.full_name ?? "", headline: p?.headline ?? "", location: p?.location ?? "",
      target_roles: (p?.target_roles ?? []).join(", "), remote_preference: p?.remote_preference ?? "any",
      experience_level: p?.experience_level ?? "entry", weekly_study_hours: p?.weekly_study_hours ?? 5,
    },
  });
  const onSubmit = handleSubmit(async (v) => {
    try {
      const saved = await api<Profile>("/account/profile", {
        method: "PATCH",
        body: { ...v, target_roles: v.target_roles.split(",").map((s) => s.trim()).filter(Boolean).slice(0, 10), ...extra },
      });
      setProfile(saved);
      toast.success("Profile saved");
      onSaved?.(saved);
    } catch (e) { toast.error(errorMessage(e)); }
  });
  return (
    <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2" noValidate>
      <div className="space-y-1.5"><Label htmlFor="full_name">Full name</Label><Input id="full_name" {...register("full_name")} /></div>
      <div className="space-y-1.5"><Label htmlFor="location">Location</Label><Input id="location" placeholder="e.g. Chennai, India" {...register("location")} /></div>
      <div className="space-y-1.5 sm:col-span-2"><Label htmlFor="headline">Headline</Label><Input id="headline" placeholder="e.g. Final-year IT student focused on full-stack development" {...register("headline")} /></div>
      <div className="space-y-1.5 sm:col-span-2"><Label htmlFor="target_roles">Target roles <span className="text-muted-foreground">(comma separated)</span></Label>
        <Input id="target_roles" placeholder="Software Developer, Python Developer" {...register("target_roles")} /></div>
      <div className="space-y-1.5"><Label htmlFor="remote_preference">Work mode</Label>
        <NativeSelect id="remote_preference" {...register("remote_preference")}><option value="any">Any</option><option value="remote">Remote</option><option value="hybrid">Hybrid</option><option value="onsite">On-site</option></NativeSelect></div>
      <div className="space-y-1.5"><Label htmlFor="experience_level">Experience level</Label>
        <NativeSelect id="experience_level" {...register("experience_level")}><option value="student">Student</option><option value="entry">Entry level</option><option value="mid">Mid level</option><option value="senior">Senior</option></NativeSelect></div>
      <div className="space-y-1.5"><Label htmlFor="weekly_study_hours">Weekly study time (hours)</Label>
        <Input id="weekly_study_hours" type="number" min={1} max={60} aria-invalid={!!errors.weekly_study_hours} {...register("weekly_study_hours")} />
        <FieldError message={errors.weekly_study_hours?.message} /></div>
      <div className="flex items-end sm:col-span-2"><Button type="submit" loading={isSubmitting}>{submitLabel}</Button></div>
    </form>
  );
}
