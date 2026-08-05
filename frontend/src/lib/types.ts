// Shared types mirroring the backend contracts.

export type UserRole = "user" | "admin";

export interface UserPublic {
  id: string;
  email: string;
  full_name: string | null;
  role: UserRole;
  is_active: boolean;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface SourceCard {
  index: number;
  chunk_id: string;
  document_id: number;
  loai_van_ban: string | null;
  so_ky_hieu: string | null;
  co_quan_ban_hanh: string | null;
  hierarchical_path: string;
  heading: string | null;
  effective_status: string | null;
  ngay_ban_hanh: string | null;
  score: number;
  source: string;
  snippet: string;
}

export interface CitationDTO {
  loai_van_ban: string | null;
  so_ky_hieu: string | null;
  dieu: string | null;
  khoan: string | null;
  diem: string | null;
  document_id: number | null;
}

export interface ConversationPublic {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface MessagePublic {
  id: string;
  role: "user" | "assistant";
  content: string;
  meta: { sources?: SourceCard[]; citations?: CitationDTO[] } | null;
  created_at: string;
}

export interface ConversationDetail extends ConversationPublic {
  messages: MessagePublic[];
}

export interface SystemConfigRead {
  revision: number;
  updated_at: string;
  updated_by: string | null;
  values: Record<string, unknown>;
  secrets_set: Record<string, boolean>;
}

export interface PipelineJob {
  id: string;
  kind: string;
  status: "running" | "succeeded" | "failed";
  started_at: string;
  finished_at: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
}
