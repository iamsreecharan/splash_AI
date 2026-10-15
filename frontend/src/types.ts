export type Severity = "high" | "medium" | "low";
export type FeedbackKind = "useful" | "irrelevant" | "resolved";
export interface Repository {
  id: string;
  full_name: string;
  description: string;
  default_branch: string;
  language: string;
  status: "pending" | "indexing" | "ready" | "failed";
  indexed_files: number;
  indexed_chunks: number;
  last_indexed_at: string | null;
  html_url: string;
  is_demo: boolean;
}
export interface Evidence {
  path: string;
  start_line: number;
  end_line: number;
  snippet: string;
  source_url: string;
  commit_sha: string;
  role: string;
}
export interface Alert {
  id: string;
  repository_id: string;
  repository_name: string;
  title: string;
  summary: string;
  severity: Severity;
  confidence: number;
  evidence_status: "verified" | "needs_investigation";
  status: "open" | "resolved" | "dismissed";
  pr_number: number;
  pr_title: string;
  pr_url: string;
  affected_component: string;
  created_at: string;
  evidence: Evidence[];
  recommendations: string[];
  change_type: string;
  feedback: string | null;
  ai_explanation?: {
    summary?: string;
    hypotheses?: { claim: string; citation_ids: string[]; verified: boolean }[];
    recommendations?: string[];
    verified?: boolean;
    model?: string;
  } | null;
}
export interface Run {
  id: string;
  kind: "index" | "analyze";
  repository_id: string;
  repository_name: string;
  pr_number: number;
  title: string;
  status: "queued" | "running" | "completed" | "failed";
  created_at: string;
  completed_at: string | null;
  alert_count: number;
  error: string | null;
  base_sha?: string | null;
  head_sha?: string | null;
  stages: {
    name: string;
    status: string;
    summary: string;
    duration_ms: number;
  }[];
}
export interface RunPage {
  items: Run[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}
export interface Session {
  user: { id: string; name: string; email: string };
  organization: { id: string; name: string };
  mode: string;
  capabilities: { github: boolean; ai: boolean; demo: boolean };
}
export interface PublicConfig {
  mode: string;
  supabase?: { configured: boolean; url?: string; anon_key?: string };
}
export interface Overview {
  repositories: number;
  open_alerts: number;
  high_severity_alerts: number;
  analyzed_changes: number;
  recent_runs: Run[];
  recent_alerts: Alert[];
}
export interface Integrations {
  github: { configured: boolean; connected: boolean; app_name: string };
  openai: { configured: boolean; enabled: boolean; model: string };
  supabase: { configured: boolean };
  mode: string;
}
