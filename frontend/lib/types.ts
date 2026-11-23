export type Scores = {
  hook_strength: number; content_payoff: number; standalone_clarity: number; emotional_impact: number; edit_readiness: number;
};
export type AIProvider = "gemini" | "openai" | "heuristic";
export type Candidate = {
  id: string; start: number; end: number; duration: number; title: string; hook_exact_quote: string; category: string; reason: string;
  warning: string | null; scores: Scores; score: number; source_evidence: { start: number; end: number; text: string }[]; review_mode: "ai" | "heuristic";
};
export type AnalysisSettings = {
  duration_preset?: "20" | "30" | "45" | "60" | "auto" | "custom"; clip_count: number; min_duration: number; max_duration: number; target_duration: number; language: "auto" | "id" | "en" | "es" | "hi"; vocab?: string; manual_start?: number; manual_end?: number;
};
export type Media = { duration: number; width: number; height: number; fps: number; video_codec: string; audio_codec: string | null; has_audio: boolean; audio_only?: boolean };
export type SubtleQAWarning = string;
export type SubtleQAResult = { ok?: boolean; warnings?: SubtleQAWarning[]; has_preview?: boolean; safeArea?: number[] };
export type Export = { id: string; start: number; end: number; aspect: "original" | "vertical"; width: number; height: number; status: "queued" | "processing" | "ready" | "failed" | "cancelled"; error: string | null; caption_style: string; created_at: string; filename: string | null; has_captions?: boolean; has_preview?: boolean; has_subtle_json?: boolean; subtle_qa?: SubtleQAResult | null; media?: Media };
export type SourceFormat = { id: string; width: number | null; height: number | null; fps: number | null; video_codec: string | null; audio_codec: string | null; ext: string | null; size_bytes: number | null; has_video: boolean; has_audio: boolean };
export type SourceQuality = { observed_max_height: number | null; eligible_max_height: number | null; max_height: number; selected_format_ids: string[]; limits: string[] };
export type SourceMetadata = { title: string; duration: number | null; width: number | null; height: number | null; fps: number | null; formats?: SourceFormat[]; quality?: SourceQuality };
export type AnalysisDiagnostics = { max_duration: number; windows_evaluated: number; accepted_before_selection: number; selected: number; rejected: Record<string, number>; continuation_starts?: number; starts_without_complete_window?: number };
export type IngestionProgress = { phase: string; downloaded_bytes: number | null; total_bytes: number | null; total_bytes_estimate: number | null; speed_bps: number | null; eta_seconds: number | null; percent: number | null; cache_hit?: boolean; metadata?: SourceMetadata | null };
export type SourceError = { code: string; retryable: boolean; recovery: "retry" | "upload" | "settings" };
export type ReframeKeyframe = { time: number; x: number; y: number; zoom: number; manual: boolean };
export type ReframeFace = { track_id: number; bbox: [number, number, number, number]; confidence: number };
export type SpeakerOverride = {start:number;end:number;focus:"left"|"right"|"both"|"locked";track_id?:number|null};
export type SpeakerInterval = {start:number;end:number;focus:"auto"|"left"|"right"|"both"|"locked";track_id:number|null;confidence:number|null;score:number|null;source:"model"|"manual"|"fallback";reason:string;review_required:boolean};
export type ReframeShot = { id: string; start: number; end: number; focus: "auto" | "left" | "right" | "both" | "locked" | "active"; review_required: boolean; reason: string | null; decision: "single_face" | "ambiguous" | "no_face" | "manual" | "active_speaker"; speaker_confidence: number | null; speaker_intervals?:SpeakerInterval[];speaker_overrides?:SpeakerOverride[];face_samples: { time: number; faces: ReframeFace[] }[]; keyframes: ReframeKeyframe[]; auto_keyframes?: ReframeKeyframe[] };
export type ReframePlan = { id: string; version: string; source_signature: string; source_width: number; source_height: number; source_fps: number; source_duration: number; start: number; end: number; mode: "manual" | "auto_face" | "active_speaker"; smoothing: number; shots: ReframeShot[]; analysis: Record<string, unknown>; warnings: string[]; created_at?: string; updated_at?: string };
export type Job = {
  id: string; title: string; youtube_url: string | null; source_name: string | null; status: string; stage: string; progress: number | null; message: string;
  error: string | null; warnings: string[]; created_at: string; updated_at: string; media: Media | null; candidates: Candidate[]; exports: Export[];
  analysis_mode: "ai" | "heuristic" | null; analysis_provider?: AIProvider; transcript_ready: boolean; settings: AnalysisSettings | null;
  analysis_diagnostics?: AnalysisDiagnostics | null;
  ingestion?: IngestionProgress | null; source_error?: SourceError | null; source_cache_hit?: boolean;
  auto_export?: { status: "queued" | "processing" | "ready" | "skipped" | "failed"; export_id?: string; message?: string } | null;
  reframe?: { status: "queued" | "processing" | "ready" | "failed" | "cancelled"; id?: string; start: number; end: number; message?: string } | null;
};
export type UpdateStatus = {
  current_version: string;
  configured: boolean;
  feed_kind: "none" | "github_releases" | "direct_manifest";
  feed_host: string;
  auto_check: boolean;
  token_configured: boolean;
  packaged: boolean;
  can_install: boolean;
  state: "unconfigured" | "idle" | "checking" | "up_to_date" | "available" | "downloading" | "verifying" | "ready_to_install" | "installing" | "failed" | "cancelled";
  available_version: string | null;
  published_at: string | null;
  release_notes: string;
  filename: string | null;
  total_bytes: number;
  received_bytes: number;
  percent: number;
  speed_bps: number | null;
  expected_sha256: string | null;
  verified_sha256: string | null;
  verified: boolean;
  checked_at: string | null;
  message: string;
  error_code: string | null;
};
export type SubtleTweaksState = {
  accent?: string | null;
  size_pct?: number | null;
  y?: number | null;
  case?: "upper" | "sentence" | "lower" | null;
  shadow?: boolean | null;
};

export type SubtleManualPick = { page: number; word_index: number; reason?: string };
export type SubtleManualDrop = { page: number; word_index: number };

export type SubtleOptionsState = {
  style: string;
  variant: string;
  emphasis: "auto" | "numbers" | "off";
  emphasis_words: string[];
  numbers: "auto" | "indonesian" | "western" | "indian" | "european" | "off";
  behind: "auto" | "on" | "off";
  matte_quality: "fast" | "balanced" | "accurate";
  placement: "auto" | "off";
  cuts: "auto" | "off";
  legibility: "auto" | "off";
  palette: "auto" | "off";
  clean_fillers: boolean;
  hinglish: boolean;
  max_words: number | null;
  canvas: "9:16" | "16:9" | "1:1" | "4:5";
  canvas_bg: string;
  vocab: string;
  replacements: Record<string, string>;
  lyrics: string;
  tweaks: SubtleTweaksState;
  manual_picks: SubtleManualPick[];
  manual_drops: SubtleManualDrop[];
  clear_auto_emphasis: boolean;
  page_edits: Record<string, string>;
  custom_recipe: Record<string, unknown> | null;
};

export type SubtleStyleMeta = {
  name: string;
  label: string;
  description: string;
  group: string;
  category: string;
  behind_enabled: boolean;
  backdrop_blur: boolean;
  has_container: boolean;
  subject_timeline: boolean;
  has_auto_palette: boolean;
  default_accent: string | null;
  variants: string[];
  default_case: "upper" | "sentence" | "lower";
  default_y: number;
};

export type SegmentationStatus = {
  available: boolean;
  bundled_ready: boolean;
  modnet_ready: boolean;
  modnet_state: "missing" | "downloading" | "verifying" | "ready" | "failed" | "cancelled";
  modnet_percent: number;
  modnet_total_bytes: number;
  modnet_received_bytes?: number;
  active_tier: string;
  message: string;
  error?: string | null;
};

export type RendererStatus = {
  available: boolean;
  node: boolean;
  script: boolean;
  bundle: boolean;
  browser: boolean;
  reason?: string | null;
};

export type SubtleStylesResponse = {
  styles: SubtleStyleMeta[];
  segmentation: SegmentationStatus;
  renderer?: RendererStatus;
};

export type SubtlePreviewResponse = {
  captions: Record<string, unknown>;
  resolved_style: Record<string, unknown>;
  srt: string;
  vtt: string;
  notes: string[];
  palette?: Record<string, string | number> | null;
  legibility_summary?: Record<string, unknown> | null;
  placement?: Record<string, unknown> | null;
  beats?: { bpm: number; times: number[]; downbeats: number[] } | null;
  cuts: number[];
  behind_active: boolean;
  backdrop_blur: boolean;
  lyrics_stats?: Record<string, number> | null;
  style_list_count: number;
};

export type Health = {
  status: string; ffmpeg: boolean; ffprobe: boolean; whisper: boolean; openai: boolean; gemini?: boolean;
  ai?: { provider: AIProvider; configured: boolean; model: string; status?: string };
  reframe?: { available: boolean; manual: boolean; auto_face: boolean; active_speaker: boolean; device: string; reason?: string };
  segmentation?: SegmentationStatus;
  renderer?: RendererStatus;
  whisper_model: string; max_upload_mb: number; storage_limit_gb: number;
  youtube_ingest?: { enabled: boolean; available: boolean; reason: string };
  app_version?: string; updates?: UpdateStatus;
};

export const activeStatuses = new Set(["queued", "probing", "transcribing", "analyzing", "exporting", "validating", "getting_metadata", "retrieving_source", "merging_media", "verifying_media", "reframing"]);
