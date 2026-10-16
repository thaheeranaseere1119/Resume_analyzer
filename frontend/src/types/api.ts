export interface Profile {
  full_name: string | null;
  headline: string | null;
  location: string | null;
  target_roles: string[];
  preferred_locations: string[];
  remote_preference: "remote" | "hybrid" | "onsite" | "any" | null;
  experience_level: "student" | "entry" | "mid" | "senior" | null;
  weekly_study_hours: number | null;
  llm_consent: boolean;
  llm_consent_at: string | null;
  onboarding_completed: boolean;
}

export interface User {
  id: string;
  email: string;
  is_admin: boolean;
  created_at: string;
  profile: Profile | null;
}

export interface AuthResponse {
  user: User;
  csrf_token: string;
  access_expires_in: number;
}

export interface SessionInfo {
  id: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string;
  user_agent: string | null;
  current: boolean;
}

export interface UploadInfo {
  id: string;
  original_filename: string;
  file_format: "pdf" | "docx";
  size_bytes: number;
  page_count: number | null;
  scanned: boolean;
  ocr_confidence: number | null;
  status: "pending" | "processing" | "ready" | "failed";
  error: string | null;
  created_at: string;
}

export interface ResumeSummary {
  id: string;
  title: string;
  status: "processing" | "ready" | "failed";
  current_version_id: string | null;
  created_at: string;
  updated_at: string;
  version_count: number;
  file_format: "pdf" | "docx" | null;
  original_filename: string | null;
}

export interface Block {
  stable_id: string;
  section_id: string | null;
  position: number;
  kind: "contact" | "paragraph" | "bullet" | "entry_header" | "skills_line";
  text: string;
  entry_key: string | null;
  page: number | null;
  char_start: number | null;
  char_end: number | null;
  confidence: number;
  user_corrected: boolean;
}

export interface Section {
  id: string;
  section_type: string;
  heading: string;
  position: number;
  page: number | null;
  confidence: number;
  warnings: string[];
}

export interface ParseWarning { severity: "info" | "warning" | "error"; code: string; section: string | null; message: string }

export interface VersionSummary {
  id: string;
  parent_id: string | null;
  version_number: number;
  label: string;
  source: string;
  document_format: "pdf" | "docx";
  job_description_id: string | null;
  created_at: string;
}

export interface Version extends VersionSummary {
  page_count: number | null;
  parse_warnings: ParseWarning[];
  profile: { name?: string | null; email?: string | null; phone?: string | null; location?: string | null; links?: string[] };
  sections: Section[];
  blocks: Block[];
}

export interface ResumeDetail extends ResumeSummary {
  upload: UploadInfo | null;
  current_version: Version | null;
}

export type DocPreview =
  | { format: "pdf"; pages: string[]; fidelity: string }
  | { format: "docx"; html: string; fidelity: string };
