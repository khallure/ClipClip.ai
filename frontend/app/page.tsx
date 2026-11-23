"use client";

import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { ArrowDownToLine, ArrowLeft, ArrowRight, AudioLines, Captions, Check, CheckCheck, ChevronDown, ChevronRight, CircleHelp, Clock3, Cpu, Download, Ellipsis, ExternalLink, Eye, FileText, Film, FolderClock, FolderOpen, HardDrive, Info, Layers3, Link2, LoaderCircle, Maximize, MoreHorizontal, Pause, Play, Plus, RotateCcw, Scissors, Settings2, ShieldCheck, SlidersHorizontal, Sparkles, Square, Trash2, UploadCloud, Video, WandSparkles, X } from "lucide-react";
import { api, parseTime, timecode, upload } from "@/lib/api";
import { activeStatuses, type AIProvider, type AnalysisSettings, type Candidate, type Export, type Health, type Job, type SubtleOptionsState, type SubtlePreviewResponse } from "@/lib/types";
import { ReframeEditor, ReframePreview, type ReframeState } from "@/components/ReframeEditor";
import { PoppinsOverlay, SubtitlePanel, defaultSubtitleStyle, type CaptionDocument, type SubtitleStyle } from "@/components/PoppinsCaptions";
import { SubtleOverlay } from "@/components/SubtleOverlay";
import { SubtleStudioPanel, defaultSubtleOptions, toggleSubtleWordEmphasis } from "@/components/SubtleStudioPanel";
import type { CaptionsFile as SubtleCaptionsFile, Style as SubtleStyle } from "@/lib/subtle/engine";
import { ModelSetup } from "@/components/ModelSetup";
import { UpdatePanel } from "@/components/UpdatePanel";

const scoreLabels = [
  ["hook_strength", "Kekuatan pembuka", 30], ["content_payoff", "Kekuatan penutup", 25], ["standalone_clarity", "Kejelasan konteks", 20], ["emotional_impact", "Dampak emosi", 15], ["edit_readiness", "Kesiapan edit", 10],
] as const;
const stageLabels: Record<string, string> = { reframing: "Mendeteksi shot dan wajah lokal", validating: "Memeriksa tautan dan akses", getting_metadata: "Membaca informasi sumber", retrieving_source: "Mengambil video dan audio", merging_media: "Menggabungkan video dan audio", verifying_media: "Memeriksa sumber yang diambil", awaiting_upload: "Menunggu sumber", uploaded: "Sumber tersedia", queued: "Dalam antrean", probing: "Memeriksa video", transcribing: "Mentranskripsi ucapan", analyzing: "Mencari klip terbaik", ready: "Siap diedit", exporting: "Menyiapkan klip", blocked: "Perlu diperiksa", failed: "Pemrosesan gagal", cancelled: "Dibatalkan" };
const sourceStages = new Set(["validating", "getting_metadata", "retrieving_source", "merging_media", "verifying_media"]);

function reportedNumber(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function sourceBytes(value: number | null | undefined): string {
  const number = reportedNumber(value);
  if (number === null) return "Belum diketahui";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = number === 0 ? 0 : Math.min(units.length - 1, Math.floor(Math.log(number) / Math.log(1000)));
  return `${(number / 1000 ** index).toLocaleString("id-ID", { maximumFractionDigits: index ? 2 : 0 })} ${units[index]}`;
}

function remainingTime(value: number | null | undefined): string {
  const seconds = reportedNumber(value);
  if (seconds === null) return "Belum diketahui";
  const rounded = Math.round(seconds);
  return rounded < 60 ? `${rounded} dtk` : rounded < 3600 ? `${Math.floor(rounded / 60)} m ${rounded % 60} dtk` : `${Math.floor(rounded / 3600)} j ${Math.floor(rounded % 3600 / 60)} m`;
}

function SourceProgressStats({ job }: { job: Job }) {
  const progress = job.ingestion;
  if (!progress || !sourceStages.has(job.status)) return null;
  const downloaded = reportedNumber(progress.downloaded_bytes);
  const exactTotal = reportedNumber(progress.total_bytes);
  const estimate = reportedNumber(progress.total_bytes_estimate);
  const transferring = progress.phase === "download" && job.status === "retrieving_source";
  const speed = transferring ? reportedNumber(progress.speed_bps) : null;
  const eta = transferring ? reportedNumber(progress.eta_seconds) : null;
  if (downloaded === null && exactTotal === null && estimate === null && speed === null && eta === null) return null;
  return <dl className="ingestion-stats" aria-label="Statistik pengambilan media">
    <div><dt>Diterima</dt><dd>{sourceBytes(downloaded)}{exactTotal !== null ? <span> / {sourceBytes(exactTotal)}</span> : estimate !== null ? <span> / ±{sourceBytes(estimate)}</span> : null}</dd></div>
    <div><dt>Kecepatan</dt><dd>{speed === null ? "—" : `${sourceBytes(speed)}/dtk`}</dd></div>
    <div><dt>Perkiraan sisa</dt><dd>{eta === null ? "—" : remainingTime(eta)}</dd></div>
  </dl>;
}

const qualityLimits: Record<string, string> = {
  resolution_cap: "Stream di atas batas resolusi pengambilan dilewati.",
  storage_quota: "Stream yang melampaui batas ukuran dilewati.",
  unsupported_transport: "Sebagian stream memakai pengiriman yang tidak didukung adapter.",
  unsupported_container: "Sebagian stream memiliki codec atau wadah yang tidak didukung.",
  missing_audio: "Sebagian video tidak memiliki pasangan audio yang dapat diambil.",
  drm: "Stream yang dilindungi DRM dilewati.",
  unknown_dimensions: "Sebagian stream tidak melaporkan dimensi.",
};

const rejectionLabels: Record<string, string> = {
  no_payoff_evidence: "Belum ada hasil atau kesimpulan yang cukup jelas",
  weak_editorial_evidence: "Bukti pembuka dan isi terlalu lemah",
  invalid_proposal: "Usulan gagal pemeriksaan batas atau kutipan",
  duplicate_overlap: "Isinya tumpang tindih dengan kandidat lain",
  editorial_review: "Usulan tidak lolos pemeriksaan editorial lanjutan",
  invalid_anchor: "Posisi kata atau kalimat tidak ditemukan",
  outside_context: "Potongan keluar dari konteks yang diperiksa",
  incomplete_or_unsupported: "Gagasan belum utuh atau bukti kurang",
  duration: "Di luar batas durasi",
  sentence_boundary: "Awal atau akhir kalimat belum utuh",
  outside_manual_range: "Di luar rentang manual",
  invalid_hook: "Pembuka tidak cocok dengan kutipan sumber",
  dangling: "Akhir masih menggantung",
  late_hook: "Bagian pembuka muncul terlalu lambat",
  invalid_description: "Judul atau alasan tidak didukung oleh sumber",
  thin_or_filler: "Isi terlalu tipis atau terlalu banyak kata pengisi",
  trailing_filler: "Potongan berakhir pada kata pengisi",
  context_dependent_start: "Pembuka merujuk ke konteks di luar klip",
};

function ClipDiagnostics({ job }: { job: Job }) {
  const diagnosis = job.analysis_diagnostics;
  if (!diagnosis || job.status !== "ready") return null;
  const reasons = Object.entries(diagnosis.rejected).filter(([, count]) => count > 0);
  return <details className="warnings">
    <summary><Info size={14} />Pemeriksaan rekomendasi<ChevronDown size={14} /></summary>
    <ul>
      <li>{diagnosis.windows_evaluated} rentang diperiksa · {diagnosis.accepted_before_selection} lolos pemeriksaan · {diagnosis.selected} dipilih</li>
      {!!diagnosis.continuation_starts && <li>Awal yang masih menyambung kalimat sebelumnya · {diagnosis.continuation_starts}</li>}
      {!!diagnosis.starts_without_complete_window && <li>Awal tanpa akhir yang utuh dalam batas durasi · {diagnosis.starts_without_complete_window}</li>}
      {reasons.map(([reason, count]) => <li key={reason}>{rejectionLabels[reason] || "Gagal pemeriksaan editorial"} · {count}</li>)}
    </ul>
  </details>;
}

function SourceInformation({ job }: { job: Job }) {
  const metadata = job.ingestion?.metadata;
  const quality = metadata?.quality;
  const cacheHit = job.source_cache_hit === true || job.ingestion?.cache_hit === true;
  if (!metadata && !job.media) return null;
  const width = reportedNumber(job.media?.width ?? metadata?.width);
  const height = reportedNumber(job.media?.height ?? metadata?.height);
  const fps = reportedNumber(job.media?.fps ?? metadata?.fps);
  const duration = reportedNumber(job.media?.duration ?? metadata?.duration);
  const formats = metadata?.formats || [];
  return <section className="source-information" aria-label="Informasi sumber video">
    <div className="source-information-heading"><Film size={17} /><div><span>{job.media ? "SUMBER TERVERIFIKASI" : "METADATA PENYEDIA"}</span><strong>{metadata?.title || job.title}</strong></div>{cacheHit && <span className="source-cache-badge"><CheckCheck size={12} />Cache lokal</span>}</div>
      <div className="source-information-values">{duration !== null && <span><Clock3 size={12} />{timecode(duration)}</span>}{width !== null && height !== null && <span>{width} × {height}</span>}{fps !== null && fps > 0 && <span>{fps.toLocaleString("id-ID", { maximumFractionDigits: 2 })} fps</span>}</div>
      {quality && <div className="source-information-values">
        {reportedNumber(quality.observed_max_height) !== null && <span>Terdeteksi hingga {quality.observed_max_height} px tinggi</span>}
        {reportedNumber(quality.eligible_max_height) !== null && <span>Memenuhi syarat hingga {quality.eligible_max_height} px tinggi</span>}
        {!!quality.selected_format_ids?.length && <span>Stream {quality.selected_format_ids.join(" + ")}</span>}
      </div>}
      {quality?.limits?.length ? <ul className="source-reuse-note">{quality.limits.flatMap(code => qualityLimits[code] ? [<li key={code}>{qualityLimits[code]}</li>] : [])}</ul> : null}
      {formats.length > 0 && <details className="source-formats"><summary>Stream yang tersedia <span>{formats.length}</span><ChevronDown size={13} /></summary><div className="source-formats-scroll"><table><thead><tr><th>Kualitas</th><th>Format / codec</th><th>Ukuran dilaporkan</th></tr></thead><tbody>{formats.map((format, index) => <tr key={`${format.id}-${index}`}><td>{format.has_video ? format.width && format.height ? `${format.width} × ${format.height}` : "Video" : "Audio"}{format.has_video && !!format.fps && <small>{format.fps.toLocaleString("id-ID", { maximumFractionDigits: 2 })} fps</small>}</td><td>{[format.ext, format.video_codec && format.video_codec !== "none" ? format.video_codec : null, format.audio_codec && format.audio_codec !== "none" ? format.audio_codec : null].filter(Boolean).join(" · ") || "—"}</td><td>{reportedNumber(format.size_bytes) === null ? "—" : sourceBytes(format.size_bytes)}</td></tr>)}</tbody></table></div><p>Kualitas sumber dipertahankan sejauh tersedia. Ukuran kanvas ekspor tidak menambah detail asli.</p></details>}
    {(cacheHit || job.source_name) && <p className="source-reuse-note"><CheckCheck size={13} />{cacheHit ? "Sumber terverifikasi dipakai kembali dari cache lokal." : "Video sumber sudah tersimpan di proyek."} Mengubah durasi klip tidak mengunduh video ulang.</p>}
  </section>;
}

function Brand({ compact = false }: { compact?: boolean }) {
  return <span className={`brand ${compact ? "compact" : ""}`}><span className="brand-symbol"><AudioLines size={22} strokeWidth={2.6} /></span><span>ClipClip<span className="brand-ai">.ai</span></span></span>;
}

function providerName(provider?: AIProvider) {
  return provider === "gemini" ? "Gemini" : "OpenAI";
}

function reviewLabel(mode: "ai" | "heuristic" | null, provider?: AIProvider) {
  return mode === "ai" ? `Ditinjau ${providerName(provider)}` : "Analisis heuristik";
}

function ReviewBadge({ mode, provider }: { mode: "ai" | "heuristic" | null; provider?: AIProvider }) {
  return <span className={`badge ${mode === "ai" ? "badge-ai" : "badge-neutral"}`}>{mode === "ai" ? <Sparkles size={11} /> : <SlidersHorizontal size={11} />}{reviewLabel(mode, provider)}</span>;
}

export default function Studio() {
  const [view, setView] = useState<"studio" | "history">("studio");
  const [health, setHealth] = useState<Health | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [sourceTab, setSourceTab] = useState<"upload" | "youtube">("youtube");
  const [youtube, setYoutube] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [task, setTask] = useState<string | null>(null);
  const [uploadPercent, setUploadPercent] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [clipCount, setClipCount] = useState(5);
  const [minDuration, setMinDuration] = useState(20);
  const [maxDuration, setMaxDuration] = useState(90);
  const [targetDuration, setTargetDuration] = useState(45);
  const [durationPreset, setDurationPreset] = useState<NonNullable<AnalysisSettings["duration_preset"]>>("20");
  const [language, setLanguage] = useState<AnalysisSettings["language"]>("auto");
  const [manual, setManual] = useState(false);
  const [rangeStart, setRangeStart] = useState("00:00");
  const [rangeEnd, setRangeEnd] = useState("03:00");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [startText, setStartText] = useState("00:00.00");
  const [endText, setEndText] = useState("00:45.00");
  const [aspect, setAspect] = useState<"original" | "vertical">("original");
  const [cropX, setCropX] = useState(.5);
  const [captionStyle, setCaptionStyle] = useState("poppins_highlight");
  const [subtitleStyle, setSubtitleStyle] = useState<SubtitleStyle>(defaultSubtitleStyle);
  const [subtleOptions, setSubtleOptions] = useState<SubtleOptionsState>(defaultSubtleOptions);
  const [subtlePreview, setSubtlePreview] = useState<{ captions: SubtleCaptionsFile | null; style: SubtleStyle | null; behindActive: boolean; meta: SubtlePreviewResponse | null }>({ captions: null, style: null, behindActive: false, meta: null });
  const [subtleMatteUrl, setSubtleMatteUrl] = useState<string | null>(null);
  const [subtleInteractiveWords, setSubtleInteractiveWords] = useState(false);
  const [qaModalExport, setQaModalExport] = useState<Export | null>(null);
  const lastActiveCaptionStyle = useRef<string>("poppins_highlight");
  const [captionDocument, setCaptionDocument] = useState<CaptionDocument | null>(null);
  const [captionRefresh, setCaptionRefresh] = useState(0);
  const [reframeState, setReframeState] = useState<ReframeState>({plan:null,dirty:false,working:false});
  const [showCaptions, setShowCaptions] = useState(true);
  const [playing, setPlaying] = useState(false);
  const [playhead, setPlayhead] = useState(0);
  const [videoError, setVideoError] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [help, setHelp] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [initializing, setInitializing] = useState(true);
  const sourceInput = useRef<HTMLInputElement>(null);
  const transcriptInput = useRef<HTMLInputElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const videoStageRef = useRef<HTMLDivElement>(null);
  const pendingDownload = useRef<string | null>(null);
  const pendingAutoExport = useRef<string | null>(null);
  const selected = job?.candidates.find(item => item.id === selectedId) || null;
  const busy = !!task || (!!job && activeStatuses.has(job.status));
  const start = parseTime(startText); const end = parseTime(endText);
  const maximumDuration = ["20", "30", "45", "60"].includes(durationPreset) ? Number(durationPreset) : Infinity;
  const validCut = Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end > start && !!job?.media && end <= job.media.duration + .01 && end - start <= maximumDuration + .000001;
  const activeJobId = job?.id;
  const activeReframe = aspect === "vertical" && reframeState.plan && Math.abs(reframeState.plan.start-start)<.00001 && Math.abs(reframeState.plan.end-end)<.00001 ? reframeState.plan : null;
  useEffect(() => { setReframeState({plan:null,dirty:false,working:false}); }, [job?.id]);
  const automaticIngestionAvailable = health?.youtube_ingest?.available === true;
  const aiProvider = health?.ai?.provider ?? (health?.gemini ? "gemini" : health?.openai ? "openai" : "heuristic");
  const aiConfigured = health?.ai?.configured ?? !!(health?.gemini || health?.openai);
  const aiDestination = aiProvider === "gemini" ? "Gemini (Google)" : "OpenAI";
  const privacyNotice = aiConfigured ? `Video diproses lokal. Teks transkrip dikirim ke ${aiDestination} saat analisis AI.` : "Video dan transkrip diproses lokal dalam mode heuristik. Analisis AI memerlukan konfigurasi penyedia.";
  const ingestionReason = health?.youtube_ingest?.reason || (health ? "Pengambilan otomatis belum tersedia. Unggah file sumber untuk melanjutkan." : "Hubungkan layanan lokal untuk memeriksa akses YouTube.");
  const audioCanvasRatio = subtleOptions.canvas === "16:9" ? 16 / 9 : subtleOptions.canvas === "1:1" ? 1 : subtleOptions.canvas === "4:5" ? 4 / 5 : 9 / 16;
  const previewRatio = job?.media?.audio_only ? audioCanvasRatio : aspect === "vertical" ? 9 / 16 : job?.media ? job.media.width / job.media.height : 16 / 9;
  const isSourceProcessing = !!job && sourceStages.has(job.status);
  const processingPercent = isSourceProcessing ? job?.ingestion?.phase === "download" ? reportedNumber(job.ingestion.percent) : null : reportedNumber(job?.progress);
  const legacySourceRestriction = !!job && (["approval", "allowlist", "not_approved"].includes(job.source_error?.code || "") || /Persetujuan platform.*belum dikonfigurasi|Daftar video yang disetujui belum dikonfigurasi|Video ini belum termasuk sumber yang disetujui/.test(job.error || ""));
  const processingMessage = legacySourceRestriction ? "Pengambilan sebelumnya tertahan oleh pengaturan lama ClipClip.ai. Coba lagi untuk memeriksa akses sumber sekarang." : job?.error || job?.message;
  const canRetry = !!job && !activeStatuses.has(job.status) && (legacySourceRestriction || (job.source_error ? job.source_error.retryable && job.source_error.recovery === "retry" : !!job.settings && (!!job.source_name || !!job.youtube_url)));
  const urlWorkflow = !!job?.youtube_url || (!job?.media && sourceTab === "youtube");

  const updateJob = useCallback((next: Job) => {
    setJob(next);
    setJobs(old => [next, ...old.filter(item => item.id !== next.id)].sort((a, b) => b.created_at.localeCompare(a.created_at)));
    window.localStorage.setItem("podcut.active-project", next.id);
  }, []);

  const connect = useCallback(async () => {
    try {
      const [capabilities, recent] = await Promise.all([api<Health>("/health"), api<Job[]>("/jobs")]);
      setHealth(capabilities); setJobs(recent); setError(null);
      const stored = window.localStorage.getItem("podcut.active-project");
      if (stored) { const found = recent.find(item => item.id === stored); if (found) setJob(found); }
    } catch (err) { setHealth(null); setError(err instanceof Error ? err.message : "Belum dapat terhubung ke layanan lokal."); }
    finally { setInitializing(false); }
  }, []);

  useEffect(() => { void connect(); }, [connect]);
  useEffect(() => {
    if (!job) return;
    if (job.youtube_url) setYoutube(job.youtube_url);
    const settings = job.settings;
    if (settings) {setDurationPreset(settings.duration_preset || "auto");setClipCount(settings.clip_count);setLanguage(settings.language);setMinDuration(settings.min_duration);setMaxDuration(settings.max_duration);setTargetDuration(settings.target_duration);}
  }, [job?.id]);
  useEffect(() => {
    const ident = pendingDownload.current;
    if (!ident || !job) return;
    const output = job.exports.find(item => item.id === ident);
    if (output?.status === "ready") {
      pendingDownload.current = null;
      const link = document.createElement("a"); link.href = `/api/jobs/${job.id}/exports/${ident}/file`; link.download = ""; document.body.appendChild(link); link.click(); link.remove();
    } else if (output && ["failed", "cancelled"].includes(output.status)) pendingDownload.current = null;
  }, [job]);
  useEffect(() => {
    if (!job || pendingAutoExport.current !== job.id) return;
    const automatic = job.auto_export;
    const output = automatic?.export_id ? job.exports.find(item => item.id === automatic.export_id) : null;
    if (automatic?.status === "ready" && output?.status === "ready") {
      pendingAutoExport.current = null;
      const link = document.createElement("a"); link.href = `/api/jobs/${job.id}/exports/${output.id}/file`; link.download = ""; document.body.appendChild(link); link.click(); link.remove();
    } else if ((automatic && ["failed", "skipped"].includes(automatic.status)) || ["blocked", "failed", "cancelled"].includes(job.status)) pendingAutoExport.current = null;
  }, [job]);
  useEffect(() => {
    if (!activeJobId) return;
    let cancelled = false;
    const timer = setInterval(async () => {
      try { const next = await api<Job>(`/jobs/${activeJobId}`); if (!cancelled) updateJob(next); }
      catch (err) { if (!cancelled) setError(err instanceof Error ? err.message : "Belum dapat memperbarui proyek ini."); }
    }, job && activeStatuses.has(job.status) ? 1500 : 6000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [activeJobId, job?.status, updateJob]);

  useEffect(() => {
    if (job?.candidates.length && !job.candidates.some(item => item.id === selectedId)) {
      const first = job.candidates[0]; setSelectedId(first.id); setStartText(timecode(first.start, true)); setEndText(timecode(first.end, true)); setPlayhead(first.start);
    }
  }, [job?.candidates, selectedId]);

  useEffect(() => {
    const player = videoRef.current;
    if (player) for (let index = 0; index < player.textTracks.length; index++) player.textTracks[index].mode = showCaptions ? "showing" : "hidden";
  }, [showCaptions, job?.transcript_ready, job?.id]);
  useEffect(() => { setVideoError(false); setPlaying(false); }, [job?.id, job?.source_name]);
  useEffect(() => {
    const changed = () => setFullscreen(document.fullscreenElement === videoStageRef.current && !!videoStageRef.current);
    document.addEventListener("fullscreenchange", changed);
    return () => document.removeEventListener("fullscreenchange", changed);
  }, []);
  useEffect(() => {
    if (captionStyle !== "none") lastActiveCaptionStyle.current = captionStyle;
  }, [captionStyle]);
  useEffect(() => {
    if (!help && !deleteId && !qaModalExport) return;
    const previous = document.activeElement as HTMLElement | null;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setHelp(false); setDeleteId(null); setQaModalExport(null); }
      if (event.key === "Tab") {
        const controls = document.querySelectorAll<HTMLElement>(".modal button:not(:disabled), .modal a[href], .modal input:not(:disabled), .modal select:not(:disabled)");
        const first = controls[0]; const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("keydown", close); previous?.focus(); };
  }, [help, deleteId, qaModalExport]);

  async function act(label: string, work: () => Promise<void>) {
    setTask(label); setError(null);
    try { await work(); } catch (err) { setError(err instanceof Error ? err.message : "Proses belum berhasil. Silakan coba lagi."); }
    finally { setTask(null); }
  }

  function newProject() {
    if (reframeState.dirty) { setError("Simpan framing sebelum berpindah klip atau proyek."); return; }
    setJob(null); setSelectedId(null); setYoutube(""); setSourceTab("youtube"); setView("studio"); setError(null); setCaptionDocument(null);
    setManual(false); setPlaying(false); setRangeStart("00:00"); setRangeEnd("03:00");
    setDurationPreset("20"); setClipCount(5); setLanguage("auto"); setMinDuration(20); setMaxDuration(90); setTargetDuration(45);
    setCaptionStyle("poppins_highlight"); setSubtitleStyle({ ...defaultSubtitleStyle }); setShowCaptions(true);
    setSubtleOptions({ ...defaultSubtleOptions }); setSubtlePreview({ captions: null, style: null, behindActive: false, meta: null }); setSubtleMatteUrl(null); setSubtleInteractiveWords(false); setQaModalExport(null);
    setAspect("original"); setCropX(.5); setStartText("00:00.00"); setEndText("00:20.00"); setPlayhead(0); pendingDownload.current = null;
    pendingAutoExport.current = null;
    window.localStorage.removeItem("podcut.active-project");
  }

  async function attachSource(file: File) {
    if (busy || reframeState.dirty) return;
    if (health && file.size > health.max_upload_mb * 1024 * 1024) { setError(`Ukuran sumber melebihi batas unggah ${health.max_upload_mb.toLocaleString("id-ID")} MB.`); return; }
    await act("uploading", async () => {
      setUploadPercent(0);
      const project = job && !job.source_name ? job : await api<Job>("/jobs", { method: "POST", body: JSON.stringify({ title: file.name.replace(/\.[^.]+$/, "") }) });
      updateJob(project);
      const next = await upload<Job>(`/jobs/${project.id}/source`, file, setUploadPercent);
      updateJob(next); setSelectedId(null); setView("studio");
      if (next.media) { setRangeEnd(timecode(Math.min(180, next.media.duration))); setEndText(timecode(Math.min(45, next.media.duration), true)); }
    });
  }

  async function analyze() {
    if (reframeState.dirty || reframeState.working) { setError("Simpan framing sebelum memulai analisis lain."); return; }
    if (durationPreset === "custom" && (![minDuration, maxDuration, targetDuration].every(value => Number.isFinite(value) && value >= 5 && value <= 90) || minDuration > maxDuration || targetDuration < minDuration || targetDuration > maxDuration)) {
      setError("Rentang khusus harus memenuhi 5 ≤ minimum ≤ target ≤ maksimum ≤ 90 detik."); return;
    }
    const settings: AnalysisSettings = { duration_preset: durationPreset, clip_count: clipCount, min_duration: durationPreset === "auto" ? 20 : minDuration, max_duration: maxDuration, target_duration: targetDuration, language };
    if (manual) {
      const from = parseTime(rangeStart); const to = parseTime(rangeEnd);
      if (!Number.isFinite(from) || !Number.isFinite(to) || from < 0 || to <= from || (job?.media && to > job.media.duration)) { setError("Masukkan rentang di dalam video, misalnya 42:00 sampai 45:00."); return; }
      settings.manual_start = from; settings.manual_end = to;
    }
    if (!job?.media) {
      if (!youtube.trim()) { setError("Masukkan tautan YouTube atau unggah video sumber."); return; }
      await act("ingesting", async () => {
        const project = job && !job.source_name && job.youtube_url === youtube.trim() ? job : await api<Job>("/jobs", { method: "POST", body: JSON.stringify({ youtube_url: youtube.trim() }) });
        updateJob(project);
        const next = await api<Job>(`/jobs/${project.id}/ingest`, { method: "POST", body: JSON.stringify({ ...settings, auto_export: true, output: { aspect, crop_x: cropX, caption_style: captionStyle, subtitle_style: subtitleStyle, ...(captionStyle === "subtle" ? { subtle: subtleOptions } : {}) } }) });
        pendingAutoExport.current = project.id;
        updateJob(next);
      });
      return;
    }
    if (job.youtube_url) {
      await act("analysis", async () => {
        const next = await api<Job>(`/jobs/${job.id}/ingest`, { method: "POST", body: JSON.stringify({ ...settings, auto_export: true, output: { aspect, crop_x: cropX, caption_style: captionStyle, subtitle_style: subtitleStyle, ...(captionStyle === "subtle" ? { subtle: subtleOptions } : {}) } }) });
        pendingAutoExport.current = job.id; updateJob(next);
      });
    } else await act("analysis", async () => updateJob(await api<Job>(`/jobs/${job.id}/analyze`, { method: "POST", body: JSON.stringify(settings) })));
  }

  async function importTranscript(file: File) {
    if (reframeState.dirty || reframeState.working) { setError("Simpan framing sebelum mengganti transkrip."); return; }
    if (!job) return;
    await act("transcript", async () => { const data = new FormData(); data.append("file", file); updateJob(await api<Job>(`/jobs/${job.id}/transcript`, { method: "POST", body: data })); setCaptionRefresh(value => value + 1); });
  }

  async function retryJob() {
    if (!job) return;
    await act("retry", async () => {
      const next = await api<Job>(`/jobs/${job.id}/retry`, { method: "POST" });
      if (next.auto_export && ["queued", "processing"].includes(next.auto_export.status)) pendingAutoExport.current = job.id;
      else {
        const queued = next.exports.find(item => item.status === "queued" || item.status === "processing");
        if (queued) pendingDownload.current = queued.id;
      }
      updateJob(next);
    });
  }

  function selectClip(candidate: Candidate) {
    if (reframeState.dirty) { setError("Simpan framing sebelum berpindah klip atau proyek."); return; }
    setSelectedId(candidate.id); setStartText(timecode(candidate.start, true)); setEndText(timecode(candidate.end, true));
    setPlayhead(candidate.start); setVideoError(false);
    const player = videoRef.current; if (player) { player.pause(); player.currentTime = candidate.start; }
  }

  function seek(value: number) {
    if (!videoRef.current || !Number.isFinite(value)) return;
    videoRef.current.currentTime = Math.max(0, Math.min(job?.media?.duration || value, value)); setPlayhead(videoRef.current.currentTime);
  }

  function togglePlayback() {
    const player = videoRef.current; if (!player) return;
    if (!player.paused) { player.pause(); return; }
    if (validCut && (player.currentTime < start || player.currentTime >= end - .05)) player.currentTime = start;
    void player.play().catch(() => setVideoError(true));
  }

  async function toggleFullscreen() {
    if (fullscreen) {
      if (document.fullscreenElement) await document.exitFullscreen();
      else setFullscreen(false);
      return;
    }
    try {
      if (videoStageRef.current?.requestFullscreen) {
        await videoStageRef.current.requestFullscreen();
        videoStageRef.current.focus();
      } else setFullscreen(true);
    } catch { setFullscreen(true); }
  }

  async function exportClip() {
    if (!job || !validCut) return;
    if (reframeState.dirty || reframeState.working) { setError("Simpan dan tunggu framing sebelum ekspor."); return; }
    if (captionStyle === "poppins_highlight" && !captionDocument?.words.length) {setError("Siapkan waktu subtitle per kata sebelum mengunduh dengan Poppins.");return;}
    if (captionStyle === "subtle" && !subtlePreview.captions?.pages?.length) {setError("Siapkan waktu subtitle per kata sebelum mengunduh dengan Subtle.");return;}
    await act("export", async () => {
      const next = await api<Job>(`/jobs/${job.id}/exports`, { method: "POST", body: JSON.stringify({ start, end, aspect, crop_x: cropX, caption_style: captionStyle, subtitle_style: subtitleStyle, duration_preset: durationPreset, ...(activeReframe ? {reframe_id: activeReframe.id} : {}), ...(captionStyle === "subtle" ? { subtle: subtleOptions } : {}) }) });
      pendingDownload.current = next.exports[next.exports.length - 1]?.id || null;
      updateJob(next);
    });
  }

  async function openProject(item: Job) {
    if (reframeState.dirty) { setError("Simpan framing sebelum berpindah klip atau proyek."); return; }
    await act("opening", async () => {
      const next = await api<Job>(`/jobs/${item.id}`); updateJob(next); setSelectedId(null); setView("studio");
      if (next.settings) { setDurationPreset(next.settings.duration_preset || "auto"); setClipCount(next.settings.clip_count); setMinDuration(next.settings.min_duration); setMaxDuration(next.settings.max_duration); setTargetDuration(next.settings.target_duration); setLanguage(next.settings.language); setManual(next.settings.manual_start != null); if (next.settings.manual_start != null) setRangeStart(timecode(next.settings.manual_start)); if (next.settings.manual_end != null) setRangeEnd(timecode(next.settings.manual_end)); }
    });
  }

  async function removeProject() {
    if (!deleteId) return;
    const id = deleteId;
    await act("deleting", async () => { await api(`/jobs/${id}`, { method: "DELETE" }); setJobs(old => old.filter(item => item.id !== id)); if (job?.id === id) { setJob(null); setSelectedId(null); window.localStorage.removeItem("podcut.active-project"); } setDeleteId(null); });
  }

  function handleDrop(event: DragEvent) { event.preventDefault(); setDragging(false); const file = event.dataTransfer.files[0]; if (file) void attachSource(file); }

  const config = <div className="analysis-settings">
    <div className="section-heading"><span><SlidersHorizontal size={15} /> Pengaturan klip</span><span className="micro">KALIMAT UTUH · PAYOFF JELAS</span></div>
    <label className="duration-label">Durasi klip <span>Preset adalah batas maksimal, bukan target potong paksa.</span></label>
    <div className="duration-presets" role="group" aria-label="Durasi klip">{(["20", "30", "45", "60", "auto", "custom"] as const).map(value => <button type="button" key={value} aria-pressed={durationPreset === value} className={durationPreset === value ? "selected" : ""} disabled={busy} onClick={() => setDurationPreset(value)}>{value === "auto" ? "Auto" : value === "custom" ? "Khusus" : `${value}s`}</button>)}</div>
    {durationPreset === "20" && <p className="duration-note">Maksimal 20,00 detik. Klip yang lebih pendek boleh dipilih bila idenya sudah lengkap.</p>}
    <div className="setting-row"><label htmlFor="clip-count">Jumlah klip <span>3–10 rekomendasi terbaik</span></label><div className="stepper"><button type="button" aria-label="Kurangi jumlah klip" onClick={() => setClipCount(Math.max(3, clipCount - 1))} disabled={busy || clipCount <= 3}>−</button><output id="clip-count">{clipCount}</output><button type="button" aria-label="Tambah jumlah klip" onClick={() => setClipCount(Math.min(10, clipCount + 1))} disabled={busy || clipCount >= 10}>+</button></div></div>
    {durationPreset === "custom" && <div className="length-fields"><label>Minimum <span className="unit-input"><input aria-label="Durasi minimum" type="number" min="5" max="90" value={minDuration} onChange={event => setMinDuration(Number(event.target.value))} disabled={busy} /><span>dtk</span></span></label><label>Target <span className="unit-input"><input aria-label="Durasi target" type="number" min="5" max="90" value={targetDuration} onChange={event => setTargetDuration(Number(event.target.value))} disabled={busy} /><span>dtk</span></span></label><label>Maksimum <span className="unit-input"><input aria-label="Durasi maksimum" type="number" min="5" max="90" value={maxDuration} onChange={event => setMaxDuration(Number(event.target.value))} disabled={busy} /><span>dtk</span></span></label></div>}
    <label className="language-select">Bahasa ucapan <span className="select-wrap"><select value={language} onChange={event => setLanguage(event.target.value as AnalysisSettings["language"])} disabled={busy}><option value="auto">Deteksi otomatis · campuran</option><option value="id">Bahasa Indonesia</option><option value="en">Bahasa Inggris</option><option value="es">Bahasa Spanyol</option><option value="hi">Bahasa Hindi / Hinglish</option></select><ChevronDown size={14} /></span></label>
    <label className="language-select">Rasio video <span className="select-wrap"><select aria-label="Rasio klip" value={aspect} onChange={event => setAspect(event.target.value as "original" | "vertical")} disabled={busy}><option value="original">Asli</option><option value="vertical">Vertikal · 9:16</option></select><ChevronDown size={14} /></span></label>
    <label className="language-select">Subtitle <span className="select-wrap"><select aria-label="Subtitle klip" value={captionStyle} onChange={event => {setCaptionStyle(event.target.value);setShowCaptions(event.target.value !== "none");}} disabled={busy}><option value="poppins_highlight">Poppins Highlight · aktif</option><option value="subtle">Subtle · 39 gaya animasi</option><option value="none">Tanpa subtitle</option></select><ChevronDown size={14} /></span></label>
    <div className="manual-box"><label className="toggle-label"><span><Clock3 size={16} /><span>Bantuan timestamp manual<small>Sudah tahu bagian yang menarik?</small></span></span><input type="checkbox" checked={manual} onChange={event => setManual(event.target.checked)} disabled={busy} /><span className="switch" aria-hidden="true" /></label>{manual && <div className="manual-fields"><label>Cari dari<input value={rangeStart} onChange={event => setRangeStart(event.target.value)} placeholder="42:00" disabled={busy} /></label><span>→</span><label>Sampai<input value={rangeEnd} onChange={event => setRangeEnd(event.target.value)} placeholder="45:00" disabled={busy} /></label><p>Rekomendasi tetap di dalam rentang ini. Gunakan mm:ss atau hh:mm:ss.</p></div>}</div>
    <button className="button primary analyze-button" disabled={busy || !health?.ffmpeg || !health?.ffprobe || (!job?.media && !(sourceTab === "youtube" && youtube.trim()))} onClick={() => void analyze()}>{task === "analysis" || task === "ingesting" ? <LoaderCircle className="spin" size={17} /> : <WandSparkles size={17} />} {job?.candidates.length ? "Cari klip terbaik lagi" : "Temukan Klip Terbaik"}<ArrowRight size={17} /></button>
    <p className="privacy-note"><ShieldCheck size={12} /> {privacyNotice}</p>
  </div>;

  return <div className="app-shell">
    <aside className="sidebar">
      <button className="brand-button" onClick={() => setView("studio")} aria-label="ClipClip.ai studio"><Brand /></button>
      <div className="workspace-label">RUANG KERJA</div>
      <nav aria-label="Navigasi utama"><button className={view === "studio" ? "nav-item active" : "nav-item"} onClick={() => setView("studio")}><Scissors size={18} />Studio klip<span className="nav-dot" /></button><button className={view === "history" ? "nav-item active" : "nav-item"} onClick={() => { setView("history"); void api<Job[]>("/jobs").then(setJobs).catch(err => setError(err.message)); }}><FolderClock size={18} />Riwayat proyek{jobs.length > 0 && <span className="nav-count">{jobs.length}</span>}</button></nav>
      <div className="sidebar-divider" />
      <div className="workspace-label recent-label">PROYEK TERBARU</div>
      <div className="recent-projects">{jobs.length ? jobs.slice(0, 4).map(item => <button key={item.id} onClick={() => void openProject(item)} disabled={!!task} className={job?.id === item.id ? "recent-project current" : "recent-project"}><Film size={14} /><span>{item.title || "Proyek tanpa judul"}</span></button>) : <p>Klip berikutnya<br />dimulai di sini.</p>}</div>
      <div className="sidebar-bottom"><div className="local-card"><div className="local-card-icon"><HardDrive size={17} /></div><div><strong>Studio di komputer Anda</strong><span>File tersimpan lokal.</span></div><span className="tiny-dot" /></div><UpdatePanel initial={health?.updates} /><button className="help-button" onClick={() => setHelp(true)}><CircleHelp size={16} />Panduan singkat<ExternalLink size={12} /></button><div className="sidebar-version"><span>ClipClip.ai</span><span>v{health?.app_version || "1.0.0"} · LOCAL</span></div></div>
    </aside>

    <div className="main-shell">
      <header className="topbar"><div className="breadcrumb"><span>Ruang kerja</span><ChevronRight size={13} /><strong>{view === "history" ? "Riwayat proyek" : "Studio klip"}</strong></div><div className="topbar-actions"><span className={`connection ${health ? "online" : "offline"}`}><span />{initializing ? "Menghubungkan…" : health ? "Layanan lokal terhubung" : "Layanan lokal terputus"}</span>{!health && !initializing && <button className="text-button" onClick={() => void connect()}>Hubungkan lagi</button>}<span className="topbar-divider" /><button className="icon-button" onClick={() => setHelp(true)} aria-label="Buka panduan"><CircleHelp size={18} /></button><div className="avatar" aria-label="Ruang kerja lokal">PC</div></div></header>

      <main>
        {health && <ModelSetup onReady={connect} />}
        {error && <div className="alert alert-error" role="alert"><Info size={17} /><span>{error}</span><button aria-label="Tutup pesan" className="icon-button" onClick={() => setError(null)}><X size={15} /></button></div>}

        {view === "history" ? <>
          <div className="page-heading history-heading"><div><div className="eyebrow"><FolderClock size={13} />KOLEKSI LOKAL</div><h1>Semua proyek.<br className="mobile-break" /> Satu tempat.</h1><p>Lanjutkan proyek Anda. Video sumber dan hasil ekspor tersimpan lokal.</p></div><button className="button primary" onClick={newProject} disabled={!!task}><Plus size={17} />Proyek baru</button></div>
          <section className="panel history-panel"><div className="panel-header"><h2>Riwayat proyek</h2><span className="pill">{jobs.length} proyek</span></div>{!jobs.length ? <div className="history-empty"><FolderOpen size={38} /><h3>Belum ada proyek.</h3><p>Proyek akan muncul setelah Anda menambahkan video sumber.</p><button className="button secondary" onClick={newProject}>Buat proyek pertama<ArrowRight size={15} /></button></div> : <div className="history-list">{jobs.map(item => <div className="history-row" key={item.id}><div className="history-thumbnail"><Film size={22} /></div><button className="history-open" onClick={() => void openProject(item)} disabled={!!task}><strong>{item.title || "Proyek tanpa judul"}</strong><span>{new Date(item.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })} <i>·</i> {item.media ? `${timecode(item.media.duration)} sumber` : "Sumber belum tersedia"} <i>·</i> {item.candidates.length} klip</span></button><span className={`status-pill status-${item.status}`}>{activeStatuses.has(item.status) && <LoaderCircle size={12} className="spin" />}{stageLabels[item.status] || item.status}</span><button className="icon-button delete-button" aria-label={`Hapus ${item.title}`} disabled={!!task || activeStatuses.has(item.status)} onClick={() => setDeleteId(item.id)}><Trash2 size={16} /></button><button className="icon-button" aria-label={`Buka ${item.title}`} onClick={() => void openProject(item)} disabled={!!task}><ArrowRight size={17} /></button></div>)}</div>}</section>
        </> : <>
          <div className="page-heading"><div><div className="eyebrow"><span className="eyebrow-line" />STUDIO KLIP PODCAST</div><h1>{job?.candidates.length ? <>Ide besar.<span> Klip bermakna.</span></> : <>Podcast panjang.<br /><span>Klip berkesan.</span></>}</h1><p>{job?.candidates.length ? "Rekomendasi siap. Periksa konteks, rapikan klip, lalu ekspor." : "Temukan bagian podcast dengan pembuka kuat dan penutup yang utuh."}</p></div><div className="heading-side">{job ? <button className="button secondary" onClick={newProject} disabled={busy}><Plus size={15} />Proyek baru</button> : <div className="edition-mark"><AudioLines size={28} /><span>UNTUK<br /><strong>CERITA BERMAKNA</strong></span></div>}</div></div>

          <div className="workflow-steps" aria-label="Alur kerja"><div className={job?.media ? "workflow-step complete" : "workflow-step current"}><span>{job?.media ? <Check size={13} /> : "01"}</span><strong>Tambah sumber</strong></div><div className="step-connector" /><div className={job?.candidates.length ? "workflow-step complete" : job?.media ? "workflow-step current" : "workflow-step"}><span>{job?.candidates.length ? <Check size={13} /> : "02"}</span><strong>Temukan klip</strong></div><div className="step-connector" /><div className={job?.candidates.length ? "workflow-step current" : "workflow-step"}><span>03</span><strong>Edit dan ekspor</strong></div><div className="workflow-right"><ShieldCheck size={13} /> Proyek tersimpan lokal</div></div>

          {job && (activeStatuses.has(job.status) || ["blocked", "failed", "cancelled"].includes(job.status)) && <section className={`processing-banner ${["blocked", "failed"].includes(job.status) ? "needs-attention" : ""}`} aria-live="polite">
            <div className="processing-icon">{activeStatuses.has(job.status) ? <LoaderCircle size={23} className="spin" /> : <Info size={23} />}</div>
            <div className="processing-copy"><strong>{stageLabels[job.status] || job.stage}</strong><p>{processingMessage}</p>
              {activeStatuses.has(job.status) && <div className={`progress-track ${processingPercent === null ? "indeterminate" : ""}`}><span style={{ width: processingPercent === null ? "35%" : `${Math.max(0, Math.min(100, processingPercent))}%` }} /></div>}
              <SourceProgressStats job={job} />
            </div>
            <div className="processing-actions">{activeStatuses.has(job.status) ? <><span className="processing-percent">{processingPercent === null ? "Memproses…" : `${processingPercent.toLocaleString("id-ID", { maximumFractionDigits: 1 })}%`}</span><button className="button ghost small" disabled={!!task} onClick={() => void act("cancel", async () => updateJob(await api<Job>(`/jobs/${job.id}/cancel`, { method: "POST" })))}><Square size={12} />Batalkan</button></> : <>
              {job.youtube_url && !job.source_name && <button className="button primary small" disabled={!!task} onClick={() => { setSourceTab("upload"); sourceInput.current?.click(); }}><UploadCloud size={14} />Unggah video sumber</button>}
              {canRetry && <button className="button secondary small" disabled={!!task} onClick={() => void retryJob()}><RotateCcw size={14} />Coba lagi</button>}
            </>}</div>
          </section>}
          {job && <SourceInformation job={job} />}
          {job?.auto_export && <p className={`auto-export-notice ${job.auto_export.status === "failed" ? "has-error" : ""}`} role="status">{job.auto_export.status === "ready" ? <CheckCheck size={14} /> : job.auto_export.status === "queued" || job.auto_export.status === "processing" ? <LoaderCircle size={14} className="spin" /> : <Info size={14} />}{job.auto_export.message || (job.auto_export.status === "ready" ? "MP4 klip terbaik siap. Hasilnya juga tersimpan pada daftar ekspor." : job.auto_export.status === "queued" || job.auto_export.status === "processing" ? "Klip terbaik akan diekspor otomatis setelah analisis selesai." : "Ekspor otomatis belum selesai. Periksa catatan pemrosesan dan pilih klip secara manual bila diperlukan.")}</p>}

          {job && <ClipDiagnostics job={job} />}
          {job?.warnings?.length ? <details className="warnings"><summary><Info size={14} />{job.warnings.length} catatan pemrosesan<ChevronDown size={14} /></summary><ul>{job.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></details> : null}

          {!job?.candidates.length ? <div className="setup-grid">
            <section className="panel source-panel"><div className="panel-header"><div><span className="panel-kicker">LANGKAH 01</span><h2>{job?.source_name ? "Video sumber siap." : "Mulai dari video sumber"}</h2></div><span className="panel-number">01<span>/03</span></span></div>
              {job?.source_name && job.media ? <div className="attached-source"><div className="source-file-icon"><Film size={25} /></div><div><strong>{job.source_name}</strong><p>{timecode(job.media.duration)} <i>·</i> {job.media.audio_only ? "Audio Saja" : `${job.media.width} × ${job.media.height} · ${job.media.video_codec.toUpperCase()}`}</p><span className="source-ready"><CheckCheck size={12} />Sumber telah diperiksa</span></div></div> : <>
                {job?.youtube_url && <div className="reference-notice"><Link2 size={16} /><div><strong>Tautan YouTube tersimpan</strong><a href={job.youtube_url} target="_blank" rel="noreferrer">{job.youtube_url}<ExternalLink size={11} /></a><p>Tautan dan pengaturan tetap tersimpan. Jika akses sumber dibatasi, unggah file video yang boleh Anda proses pada proyek ini.</p></div></div>}
                <div className="source-tabs" role="tablist" aria-label="Pilih sumber video"><button role="tab" aria-selected={sourceTab === "upload"} className={sourceTab === "upload" ? "selected" : ""} onClick={() => setSourceTab("upload")} disabled={busy}><UploadCloud size={15} />Unggah media</button><button role="tab" aria-selected={sourceTab === "youtube"} className={sourceTab === "youtube" ? "selected" : ""} onClick={() => setSourceTab("youtube")} disabled={busy}><Link2 size={15} />Tautan YouTube</button></div>
                {sourceTab === "upload" ? <div className={`drop-zone ${dragging ? "dragging" : ""} ${busy ? "disabled" : ""}`} onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={handleDrop}><div className="upload-icon"><UploadCloud size={29} strokeWidth={1.5} /></div><h3>{task === "uploading" ? uploadPercent >= 100 ? "Memeriksa media sumber…" : `Mengunggah media… ${uploadPercent}%` : "Letakkan video atau audio di sini"}</h3><p>atau <button className="inline-button" onClick={() => sourceInput.current?.click()} disabled={busy}>pilih dari komputer</button></p><span className="format-note">MP4, MOV, MKV, WEBM, MP3, WAV, M4A{health && ` · MAKS. ${health.max_upload_mb >= 1024 ? `${(health.max_upload_mb / 1024).toFixed(0)} GB` : `${health.max_upload_mb} MB`}`}</span>{task === "uploading" && <div className="upload-progress"><span style={{ width: `${uploadPercent}%` }} /></div>}</div> : <form onSubmit={event => {event.preventDefault();void analyze();}} className="youtube-form"><label htmlFor="youtube-url">URL video YouTube</label><div className="url-input"><Link2 size={17} /><input id="youtube-url" type="url" value={youtube} onChange={event => setYoutube(event.target.value)} placeholder="https://www.youtube.com/watch?v=…" required disabled={busy} /></div><p><Info size={14} />Tempel tautan lalu pilih Temukan Klip Terbaik. Video diambil dan klip terbaik diekspor otomatis. Gunakan sumber yang boleh Anda akses dan proses; unggah video bila akses dibatasi.</p><p className="ingestion-notice">{ingestionReason}</p></form>}
              </>}
              {job?.status === "ready" && job.settings && !job.candidates.length && <div className="no-moments"><Info size={16} /><div><strong>Belum ditemukan klip dengan ide yang utuh.</strong><p>Coba durasi yang lebih panjang atau tentukan rentang waktu. Anda juga dapat mengimpor transkrip yang dikoreksi lalu menganalisis ulang.</p></div></div>}
              {job?.media && <div className="transcript-option"><div><FileText size={15} /><span>{job.transcript_ready ? "Transkrip bertimestamp siap" : "Sudah memiliki transkrip?"}<small>{job.transcript_ready ? "Analisis memakai transkrip ini kembali." : "Impor JSON atau SRT jika tersedia"}</small></span></div><button className="text-button" disabled={busy} onClick={() => transcriptInput.current?.click()}>{task === "transcript" ? "Mengimpor…" : job.transcript_ready ? "Ganti" : "Impor"}<ArrowRight size={13} /></button></div>}
              {config}
            </section>

            <aside className="setup-right"><section className="promise-card"><div className="promise-top"><span className="live-label"><span />TEMUKAN BAGIAN PALING BERARTI</span><MoreHorizontal size={19} /></div><div className="waveform-art" aria-hidden="true"><div className="waveform-glow" /><div className="waveform-lines">{Array.from({ length: 65 }, (_, i) => <span key={i} style={{ height: `${12 + Math.abs(Math.sin(i * .73) * Math.cos(i * .21)) * 80}%`, opacity: .18 + Math.abs(Math.sin(i * .32)) * .7 }} />)}</div><div className="waveform-selection"><span className="selection-handle left" /><span className="selection-handle right" /><span className="selection-label"><Scissors size={11} />KLIP PILIHAN ANDA</span></div><div className="waveform-axis"><span>PERCAKAPAN</span><span>MOMEN TERPILIH</span></div></div><div className="promise-copy"><span className="mini-label">LEBIH CEPAT MENEMUKAN CERITA.</span><h2>Temukan ceritanya.<br />Jaga konteksnya.</h2><p>Temukan pembuka yang menarik, penjelasan yang bermakna, dan penutup yang layak ditonton.</p></div><div className="promise-features"><div><Layers3 size={17} /><span>Analisis seluruh podcast<small>Setiap bagian ikut dinilai.</small></span></div><div><FileText size={17} /><span>Ucapan dan waktu asli.<small>Kutipan sumber dapat diperiksa.</small></span></div><div><Scissors size={17} /><span>Rapikan sebelum dibagikan<small>Tentukan batas klip dengan tepat.</small></span></div></div></section><div className="engine-card"><div className="engine-title"><Cpu size={16} /><strong>Layanan di komputer Anda</strong><span className={health ? "tiny-dot" : "tiny-dot muted"} /></div><div className="engine-row"><span>Penilaian klip</span><span>{aiConfigured ? `${providerName(aiProvider)} · siap dicoba` : "Analisis heuristik"}</span></div><div className="engine-row"><span>Transkripsi ucapan</span><span>{health?.whisper ? `Whisper · ${health.whisper_model}` : "Impor transkrip tersedia"}</span></div><div className="engine-row"><span>Pemrosesan video</span><span className={health?.ffmpeg && health.ffprobe ? "engine-good" : "engine-warning"}>{health?.ffmpeg && health.ffprobe ? <><Check size={12} />FFmpeg siap</> : "FFmpeg belum tersedia"}</span></div>{health && !aiConfigured && <p>AI belum dikonfigurasi. Rekomendasi memakai heuristik dan tetap memerlukan pemeriksaan Anda.</p>}{aiConfigured && <p>Kunci sudah dikonfigurasi; koneksi dan kuota diperiksa saat analisis. Hasil yang berhasil diberi label Ditinjau {providerName(aiProvider)}. Jika AI tidak tersedia, hasil tetap diberi label heuristik.</p>}</div></aside>
          </div> : <div className="results-grid">
            <div className="results-column"><section className="panel recommendation-panel"><div className="panel-header"><div><span className="panel-kicker">REKOMENDASI ANDA</span><h2>{job.candidates.length} klip untuk diperiksa</h2></div><span className="results-count">{String(job.candidates.length).padStart(2, "0")}</span></div><div className="recommendation-meta"><ReviewBadge mode={job.analysis_mode} provider={job.analysis_provider} /><span>Diurutkan menurut kualitas isi <Info size={12} /></span></div><div className="clip-list">{job.candidates.map((candidate, index) => <article className={`clip-card ${selectedId === candidate.id ? "selected" : ""}`} key={candidate.id}><button className="clip-select" onClick={() => selectClip(candidate)} aria-pressed={selectedId === candidate.id}><div className="clip-top"><span className="clip-rank">#{String(index + 1).padStart(2, "0")}</span><span className="category">{candidate.category.replaceAll("_", " ")}</span><span className="clip-duration">{candidate.duration.toFixed(2)}s</span><span className="editorial-score">{candidate.score.toFixed(1)}<small>/100</small></span></div><h3>{candidate.title}</h3><div className="clip-time"><Play size={11} fill="currentColor" />{timecode(candidate.start)}<span>→</span>{timecode(candidate.end)}<span className="clip-mode">{reviewLabel(candidate.review_mode, job.analysis_provider)}</span></div><blockquote>“{candidate.hook_exact_quote}”</blockquote><p>{candidate.reason}</p>{candidate.warning && <div className="context-warning"><Info size={12} />{candidate.warning}</div>}<span className="clip-edit-link">{selectedId === candidate.id ? "Klip sedang diedit" : "Pratinjau dan edit"}<ArrowRight size={14} /></span></button><details className="clip-evidence"><summary>Rincian skor dan kutipan sumber<ChevronDown size={12} /></summary><div className="score-breakdown">{scoreLabels.map(([key, label, weight]) => <div key={key}><span>{label}<small>Bobot {weight}%</small></span><div><span style={{ width: `${candidate.scores[key]}%` }} /></div><strong>{candidate.scores[key]}</strong></div>)}</div><p className="score-disclaimer">Skor menilai kualitas isi dan tidak menjamin viral. {candidate.review_mode === "heuristic" && "Penilaian heuristik perlu Anda periksa kembali."}</p><div className="evidence-quotes">{candidate.source_evidence.map((evidence, i) => <button key={i} onClick={() => { selectClip(candidate); seek(evidence.start); }}><span>{timecode(evidence.start)}–{timecode(evidence.end)}</span><q>{evidence.text}</q></button>)}</div></details></article>)}</div></section><details className="panel rerun-panel"><summary><SlidersHorizontal size={16} />Pengaturan analisis<ChevronDown size={15} /></summary>{config}<div className="transcript-option"><span><FileText size={14} />Transkrip disimpan lokal</span><button className="text-button" onClick={() => transcriptInput.current?.click()} disabled={busy}>Ganti transkrip</button></div></details></div>

            <div className="editor-column"><section className="panel editor-panel"><div className="panel-header"><div><span className="panel-kicker">RAPIKAN KLIP</span><h2>Pratinjau dan edit</h2></div><span className="badge badge-neutral"><span className="tiny-dot" />PRATINJAU SUMBER</span></div><div ref={videoStageRef} className={`video-stage ${fullscreen ? "expanded-stage" : ""} ${previewRatio < 1 ? "vertical-stage" : ""}`} style={job.media?.audio_only ? { aspectRatio: String(previewRatio), ...(previewRatio < 1 ? { maxHeight: "460px", maxWidth: "259px" } : {}) } : aspect === "vertical" ? { aspectRatio: "9 / 16", maxHeight: "460px", maxWidth: "259px" } : { aspectRatio: job.media ? `${job.media.width} / ${job.media.height}` : "16 / 9" }} tabIndex={0} aria-label="Pratinjau video. Spasi untuk putar atau jeda, panah untuk maju atau mundur 5 detik." onKeyDown={event => { if (event.key === "Escape" && fullscreen) { event.preventDefault(); void toggleFullscreen(); return; } if (event.target !== event.currentTarget) return; if (event.code === "Space") { event.preventDefault(); togglePlayback(); } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); seek(playhead + (event.key === "ArrowLeft" ? -5 : 5)); } }}>
              {fullscreen && <button className="fullscreen-close button secondary small" onClick={() => void toggleFullscreen()} aria-label="Tutup layar penuh"><X size={16} />Kembali ke editor</button>}<div className="video-canvas" style={{ position: "relative", flexShrink: 0, backgroundColor: job.media?.audio_only ? subtleOptions.canvas_bg : undefined, width: fullscreen ? `min(100vw, ${100 * previewRatio}vh)` : "100%", aspectRatio: job.media?.audio_only ? String(previewRatio) : aspect === "vertical" ? "9 / 16" : `${job.media?.width || 16} / ${job.media?.height || 9}` }}>{job.media?.audio_only && <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", pointerEvents: "none", color: "rgba(255,255,255,0.12)" }}><AudioLines size={44} /></div>}<video key={`${job.id}-${job.source_name}`} ref={videoRef} src={`/api/jobs/${job.id}/media`} preload="metadata" playsInline controls={false} style={job.media?.audio_only ? { width: "100%", height: "100%", position: "absolute", inset: 0, opacity: 0 } : activeReframe ? {width:"100%",height:"100%",position:"absolute",inset:0,opacity:0,pointerEvents:"none"} : aspect === "vertical" ? { width: "100%", height: "100%", objectFit: "cover", objectPosition: `${cropX * 100}% center` } : { width: "100%", height: "100%", objectFit: "contain" }} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onLoadedMetadata={() => { if (Number.isFinite(start)) seek(start); }} onTimeUpdate={event => { const player = event.currentTarget; setPlayhead(player.currentTime); if (validCut && player.currentTime >= end && !player.paused) { player.pause(); player.currentTime = end; } }} onError={() => setVideoError(true)} onClick={togglePlayback}></video>{activeReframe && !job.media?.audio_only && <ReframePreview videoRef={videoRef} plan={activeReframe} onClick={togglePlayback} />}<PoppinsOverlay videoRef={videoRef} document={captionDocument} enabled={showCaptions && captionStyle === "poppins_highlight"} /><SubtleOverlay videoRef={videoRef} captions={subtlePreview.captions} style={subtlePreview.style} clipStart={Number.isFinite(start) ? start : 0} enabled={showCaptions && captionStyle === "subtle"} behindActive={subtlePreview.behindActive} mattePreviewUrl={subtleMatteUrl} interactive={subtleInteractiveWords} onToggleWord={(pageIdx, wordIdx, _txt, emphasized) => setSubtleOptions(prev => toggleSubtleWordEmphasis(prev, pageIdx, wordIdx, emphasized))} />
              {!playing && !videoError && <button className="video-play-button" onClick={togglePlayback} aria-label="Putar klip terpilih"><Play size={24} fill="currentColor" /></button>}{videoError && <div className="video-error"><Info size={24} /><strong>Media ini belum dapat diputar di peramban.</strong><p>Format mungkin belum didukung. Ekspor MP4 H.264 untuk memeriksa hasilnya.</p></div>}{aspect === "vertical" && !job.media?.audio_only && <span className="crop-preview-label">{activeReframe ? "FRAMING PER SHOT" : "PRATINJAU BINGKAI MANUAL"}</span>}
            </div></div><div className="player-toolbar"><button className="icon-button" onClick={togglePlayback} aria-label={playing ? "Jeda pratinjau" : "Putar pratinjau"}>{playing ? <Pause size={16} /> : <Play size={16} fill="currentColor" />}</button><span className="player-time">{timecode(playhead, true)}<span>/ {timecode(job.media?.duration || 0)}</span></span><div className="player-spacer" /><button className={`icon-button ${showCaptions ? "icon-active" : ""}`} disabled={busy} onClick={() => { if (showCaptions) { setShowCaptions(false); setCaptionStyle("none"); } else { setShowCaptions(true); setCaptionStyle(lastActiveCaptionStyle.current || "poppins_highlight"); } }} aria-label={showCaptions ? "Sembunyikan subtitle" : "Tampilkan subtitle"} aria-pressed={showCaptions}><Captions size={19} /></button><button className="icon-button" aria-label="Layar penuh" onClick={() => void toggleFullscreen()}><Maximize size={16} /></button></div>
            <div className="trim-editor"><div className="trim-heading"><span><Scissors size={14} />Tentukan awal dan akhir klip</span><span>{validCut ? `${(end - start).toFixed(2)} dtk dipilih` : "Periksa waktu klip"}</span></div><div className="timeline"><div className="timeline-track"><span style={{ left: `${(Number.isFinite(start) ? start : 0) / (job.media?.duration || 1) * 100}%`, width: `${Math.max(0, (end - start) / (job.media?.duration || 1) * 100)}%` }} /></div><input type="range" min="0" max={job.media?.duration || 1} step="0.01" value={Math.min(playhead, job.media?.duration || 1)} onChange={event => seek(Number(event.target.value))} aria-label="Posisi pemutaran video" /></div><div className="timestamp-fields"><label>AWAL <span>Waktu mulai</span><div><input aria-label="Waktu awal klip" disabled={busy || reframeState.dirty} value={startText} onChange={event => setStartText(event.target.value)} onBlur={() => { if (Number.isFinite(start)) seek(start); }} /><button className="text-button" disabled={busy || reframeState.dirty} onClick={() => setStartText(timecode(playhead, true))} title="Gunakan posisi pemutaran sebagai awal">Pakai</button></div></label><span className="timestamp-arrow">→</span><label>AKHIR <span>Waktu akhir</span><div><input aria-label="Waktu akhir klip" disabled={busy || reframeState.dirty} value={endText} onChange={event => setEndText(event.target.value)} /><button className="text-button" disabled={busy || reframeState.dirty} onClick={() => setEndText(timecode(playhead, true))} title="Gunakan posisi pemutaran sebagai akhir">Pakai</button></div></label></div><div className="boundary-sliders"><label>Geser awal<input disabled={busy || reframeState.dirty} type="range" min="0" max={Math.max(0, (Number.isFinite(end) ? end : job.media?.duration || 1) - .1)} step="0.01" value={Number.isFinite(start) ? start : 0} onChange={event => { setStartText(timecode(Number(event.target.value), true)); seek(Number(event.target.value)); }} /></label><label>Geser akhir<input disabled={busy || reframeState.dirty} type="range" min={Math.min(job.media?.duration || 1, (Number.isFinite(start) ? start : 0) + .1)} max={job.media?.duration || 1} step="0.01" value={Number.isFinite(end) ? end : job.media?.duration || 1} onChange={event => setEndText(timecode(Number(event.target.value), true))} /></label></div>{!validCut && <p className="field-error">Periksa in/out dan batas durasi preset. Akhir harus setelah awal, di dalam sumber, dan tidak melampaui batas maksimal.</p>}</div>
            <div className="export-settings"><div className="section-heading"><span><Settings2 size={15} />Pengaturan ekspor</span><span className="micro">H.264 / AAC · MP4</span></div>{!job.media?.audio_only && <div className="aspect-options"><button className={aspect === "original" ? "aspect-option selected" : "aspect-option"} disabled={reframeState.dirty || reframeState.working} onClick={() => setAspect("original")}><span className="aspect-icon landscape" /><span>Asli<small>{job.media ? Math.ceil(job.media.width / 2) * 2 : "—"} × {job.media ? Math.ceil(job.media.height / 2) * 2 : "—"}</small></span>{aspect === "original" && <Check size={14} />}</button><button className={aspect === "vertical" ? "aspect-option selected" : "aspect-option"} onClick={() => setAspect("vertical")}><span className="aspect-icon portrait" /><span>Vertikal · 9:16<small>1080 × 1920</small></span>{aspect === "vertical" && <Check size={14} />}</button></div>}{aspect === "vertical" && !activeReframe && !job.media?.audio_only && <div className="crop-settings"><label>Posisi bingkai horizontal<span>{Math.round(cropX * 100)}%</span><input aria-label="Posisi potongan horizontal" type="range" min="0" max="1" step=".01" value={cropX} onChange={event => setCropX(Number(event.target.value))} /></label><p><Info size={12} />Sesuaikan bingkai agar wajah dan teks penting tetap terlihat.</p>{job.media && (job.media.width < 1080 || job.media.height < 1920) && <p>Kanvas ekspor 1080 × 1920. Ketajaman tetap mengikuti kualitas video sumber.</p>}</div>}{aspect === "vertical" && !job.media?.audio_only && <ReframeEditor key={`${job.id}:${start}:${end}`} job={job} start={start} end={end} playhead={playhead} busy={busy} videoRef={videoRef} onSeek={seek} onJob={updateJob} onState={setReframeState} />}<label className="caption-select">Subtitle dalam video<span className="select-wrap"><select value={captionStyle} onChange={event => {setCaptionStyle(event.target.value);setShowCaptions(event.target.value !== "none");}} disabled={busy}><option value="poppins_highlight">Poppins Highlight · putih + lime</option><option value="subtle">Subtle · 39 gaya animasi Motion</option><option value="none">Tanpa subtitle</option></select><ChevronDown size={14} /></span></label><button className="button primary export-button" onClick={() => void exportClip()} disabled={busy || reframeState.dirty || reframeState.working || !validCut || (captionStyle === "poppins_highlight" && !captionDocument?.words.length) || (captionStyle === "subtle" && !subtlePreview.captions?.pages?.length)}>{job.status === "exporting" || task === "export" ? <LoaderCircle size={17} className="spin" /> : <ArrowDownToLine size={17} />}{job.status === "exporting" ? "Menyiapkan MP4…" : "Unduh Klip MP4"}<span>{job.media?.audio_only ? subtleOptions.canvas : aspect === "vertical" ? "9:16" : "ASLI"}</span></button><p className="export-note">{captionStyle === "subtle" ? "Pratinjau dan ekspor memakai mesin Subtle + Remotion alpha yang sama." : "Pratinjau dan ekspor memakai Poppins serta waktu kata yang sama."}</p><p className="export-note">Detail hasil mengikuti resolusi sumber dan area crop.</p></div><SubtitlePanel job={job} start={start} end={end} aspect={aspect} style={subtitleStyle} enabled={captionStyle === "poppins_highlight"} busy={busy} refreshKey={captionRefresh} onStyle={setSubtitleStyle} onDocument={setCaptionDocument} onJob={updateJob} />{captionStyle === "subtle" && <SubtleStudioPanel job={job} start={Number.isFinite(start) ? start : 0} end={Number.isFinite(end) ? end : 0} aspect={aspect} cropX={cropX} options={subtleOptions} busy={busy} refreshKey={captionRefresh} health={health} interactiveWords={subtleInteractiveWords} onInteractiveWordsChange={setSubtleInteractiveWords} onOptionsChange={setSubtleOptions} onPreviewState={setSubtlePreview} onMatteUrlReady={setSubtleMatteUrl} onJob={updateJob} />}
          </section>
          {job.exports.length > 0 && <section className="panel exports-panel"><div className="panel-header"><h2>Hasil ekspor</h2><span className="pill">{job.exports.length}</span></div>{[...job.exports].reverse().map(item => <div className="export-row" key={item.id}><div className={`export-file-icon ${item.status === "ready" ? "ready" : ""}`}>{item.status === "ready" ? <CheckCheck size={19} /> : item.status === "processing" || item.status === "queued" ? <LoaderCircle size={19} className="spin" /> : <Film size={19} />}</div><div className="export-row-copy"><strong>{item.aspect === "vertical" ? "Klip vertikal" : "Klip rasio asli"}<span>{item.width} × {item.height}</span>{item.caption_style === "subtle" && item.subtle_qa && <span className={`pill ${item.subtle_qa.ok ? "" : "status-blocked"}`}>{item.subtle_qa.ok ? "QA OK" : `${item.subtle_qa.warnings?.length || 1} Catatan`}</span>}</strong><p>{timecode(item.start)}–{timecode(item.end)} <i>·</i> {item.status === "ready" ? "MP4 terverifikasi siap" : item.status === "queued" ? "Dalam antrean" : item.status === "processing" ? "Menyiapkan…" : item.status}{item.media && <> · {item.media.duration.toLocaleString("id-ID", { maximumFractionDigits: 2 })} dtk · {item.media.fps.toLocaleString("id-ID", { maximumFractionDigits: 2 })} fps · {item.media.video_codec.toUpperCase()}{item.media.audio_codec ? " / " + item.media.audio_codec.toUpperCase() : ""}</>}{item.caption_style !== "none" ? ` · Subtitle ${item.caption_style === "subtle" ? "Subtle" : "menyatu dengan video"}` : ""}</p>{item.error && <p className="field-error">{item.error}</p>}</div>{item.status === "ready" && <div className="export-downloads"><a href={`/api/jobs/${job.id}/exports/${item.id}/file`} className="icon-button download-mp4" download aria-label="Unduh MP4"><Download size={17} /></a>{(item.has_captions ?? job.transcript_ready) && <a href={`/api/jobs/${job.id}/exports/${item.id}/captions.srt`} className="text-button" download>SRT</a>}{item.caption_style === "poppins_highlight" && <><a href={`/api/jobs/${job.id}/exports/${item.id}/captions.vtt`} className="text-button" download>VTT</a><a href={`/api/jobs/${job.id}/exports/${item.id}/captions.ass`} className="text-button" download>ASS</a></>}{item.caption_style === "subtle" && <><a href={`/api/jobs/${job.id}/exports/${item.id}/captions.vtt`} className="text-button" download>VTT</a>{item.has_subtle_json && <a href={`/api/jobs/${job.id}/exports/${item.id}/captions.json`} className="text-button" download>JSON</a>}{item.has_preview && <button type="button" className="text-button" onClick={() => setQaModalExport(item)}><Eye size={13} /> QA</button>}</>}</div>}</div>)}</section>}
          <div className="editor-footnote"><ShieldCheck size={13} />Hasil ekspor tersimpan di folder proyek lokal.</div></div>
          </div>}
        </>}

        <footer className="page-footer"><Brand compact /><span>Cerita bermakna, klip berkualitas.</span><span className="footer-right">LOKAL. KENDALI DI TANGAN ANDA.</span></footer>
      </main>
    </div>
    <input ref={sourceInput} type="file" accept="video/*,audio/*,.mkv,.avi,.m4v,.mov,.mp4,.webm,.mp3,.wav,.m4a,.aac,.flac,.ogg" className="visually-hidden" aria-label="Pilih media sumber" onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void attachSource(file); }} />
    <input ref={transcriptInput} type="file" accept=".json,.srt" className="visually-hidden" aria-label="Pilih transkrip bertimestamp" onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void importTranscript(file); }} />
    {help && <div className="modal-overlay" onClick={() => setHelp(false)}><section className="modal help-modal" role="dialog" aria-modal="true" aria-labelledby="help-title" onClick={event => event.stopPropagation()}><button autoFocus className="icon-button modal-close" aria-label="Tutup panduan" onClick={() => setHelp(false)}><X size={20} /></button><span className="eyebrow"><Scissors size={14} />PANDUAN KLIP</span><h2 id="help-title">Dari podcast menjadi klip.</h2><ol className="help-steps"><li><span>01</span><div><strong>Tambahkan video sumber</strong><p>Tempel tautan YouTube untuk pengambilan otomatis yang tersedia dan diizinkan, atau unggah video yang boleh Anda proses. Bila akses terblokir, tautan dan pengaturan tetap tersimpan.</p></div></li><li><span>02</span><div><strong>Temukan klip terbaik</strong><p>Pilih jumlah klip dan batas durasinya. Gunakan rentang waktu bila diperlukan. Whisper mempertahankan bahasa ucapan; penggunaan pertama dapat mengunduh model transkripsi.</p></div></li><li><span>03</span><div><strong>Edit dan ekspor</strong><p>Periksa kutipan dan konteksnya. Sesuaikan batas awal/akhir, bingkai, serta subtitle Poppins, lalu unduh MP4 yang sudah diverifikasi.</p></div></li></ol><div className="help-callout"><Info size={17} /><p>Tanpa konfigurasi AI, rekomendasi diberi label analisis heuristik. Skor menilai kualitas isi dan tidak menjamin viral. Penilaian AI mengirim teks transkrip ke {aiConfigured ? aiDestination : "penyedia AI yang dikonfigurasi"}; video tetap diproses lokal. Biaya atau kuota mengikuti penyedia. Kunci yang tersimpan belum membuktikan koneksi berhasil; periksa label hasil analisis.</p></div><p className="help-keyboard">Pada pratinjau video: <kbd>Spasi</kbd> putar / jeda <kbd>←</kbd> <kbd>→</kbd> geser 5 detik.</p><button className="button primary" onClick={() => setHelp(false)}>Kembali ke studio<ArrowRight size={16} /></button></section></div>}
    {deleteId && <div className="modal-overlay" onClick={() => setDeleteId(null)}><section className="modal delete-modal" role="dialog" aria-modal="true" aria-labelledby="delete-title" onClick={event => event.stopPropagation()}><div className="delete-modal-icon"><Trash2 size={23} /></div><h2 id="delete-title">Hapus proyek lokal ini?</h2><p>Video sumber yang diunggah, transkrip, dan hasil ekspor di dalam ClipClip.ai akan dihapus. File asli di luar aplikasi tetap tersedia.</p><div className="modal-actions"><button autoFocus className="button secondary" onClick={() => setDeleteId(null)} disabled={task === "deleting"}>Simpan proyek</button><button className="button danger" onClick={() => void removeProject()} disabled={task === "deleting"}>{task === "deleting" ? <LoaderCircle size={16} className="spin" /> : <Trash2 size={16} />}Hapus proyek</button></div></section></div>}
    {qaModalExport && job && <div className="modal-overlay" onClick={() => setQaModalExport(null)}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="qa-modal-title" style={{ maxWidth: 820 }} onClick={event => event.stopPropagation()}><button autoFocus className="icon-button modal-close" aria-label="Tutup pratinjau QA" onClick={() => setQaModalExport(null)}><X size={20} /></button><span className="eyebrow"><Eye size={14} />INSPEKSI VISUAL SUBTLE</span><h2 id="qa-modal-title">Contact Sheet QA Ekspor</h2><img src={`/api/jobs/${job.id}/exports/${qaModalExport.id}/preview.jpg`} alt="Contact sheet QA Subtle" style={{ width: "100%", borderRadius: 8, border: "1px solid #354b26", marginBottom: 14 }} />{qaModalExport.subtle_qa?.warnings?.length ? <ul className="source-reuse-note" style={{ flexDirection: "column", alignItems: "flex-start" }}>{qaModalExport.subtle_qa.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul> : <p style={{ fontSize: 11, color: "var(--lime)", margin: 0 }}>Semua sampel bingkai lulus pemeriksaan keterbacaan & area aman.</p>}</section></div>}
  </div>;
}


