"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCheck,
  Download,
  Eye,
  LoaderCircle,
  MousePointerClick,
  Music,
  Palette,
  RotateCcw,
  Sliders,
  Sparkles,
  UserCheck,
  Wand2,
} from "lucide-react";
import { api } from "@/lib/api";
import type { CaptionsFile, Style } from "@/lib/subtle/engine";
import { buildOverride, deepMerge, prepareCaptions, type Preset } from "@/lib/subtle/tweaks";
import type {
  Health,
  Job,
  RendererStatus,
  SegmentationStatus,
  SubtleOptionsState,
  SubtlePreviewResponse,
  SubtleStyleMeta,
  SubtleStylesResponse,
} from "@/lib/types";
import styles from "./SubtleStudioPanel.module.css";

export const defaultSubtleOptions: SubtleOptionsState = {
  style: "glide",
  variant: "",
  emphasis: "auto",
  emphasis_words: [],
  numbers: "auto",
  behind: "auto",
  matte_quality: "balanced",
  placement: "auto",
  cuts: "auto",
  legibility: "auto",
  palette: "auto",
  clean_fillers: false,
  hinglish: false,
  max_words: null,
  canvas: "9:16",
  canvas_bg: "#0E0E10",
  vocab: "",
  replacements: {},
  lyrics: "",
  tweaks: {},
  manual_picks: [],
  manual_drops: [],
  clear_auto_emphasis: false,
  page_edits: {},
  custom_recipe: null,
};

export function toggleSubtleWordEmphasis(
  options: SubtleOptionsState,
  pageIndex: number,
  wordIndex: number,
  currentlyEmphasized: boolean,
): SubtleOptionsState {
  const same = (item: { page: number; word_index: number }) =>
    item.page === pageIndex && item.word_index === wordIndex;
  if (currentlyEmphasized) {
    return {
      ...options,
      manual_picks: options.manual_picks.filter((i) => !same(i)),
      manual_drops: [...options.manual_drops.filter((i) => !same(i)), { page: pageIndex, word_index: wordIndex }],
    };
  }
  return {
    ...options,
    manual_drops: options.manual_drops.filter((i) => !same(i)),
    manual_picks: [
      ...options.manual_picks.filter((i) => !same(i)),
      { page: pageIndex, word_index: wordIndex, reason: "dipilih pengguna" },
    ],
  };
}

type StudioTab = "styles" | "smart" | "vision" | "recipe";

const CATEGORIES = [
  { id: "all", label: "Semua" },
  { id: "behind", label: "Behind" },
  { id: "editorial", label: "Editorial" },
  { id: "container", label: "Kartu" },
  { id: "kinetic", label: "Kinetik" },
  { id: "music", label: "Musik" },
];

export function SubtleStudioPanel({
  job,
  start,
  end,
  aspect,
  cropX,
  options,
  busy,
  refreshKey,
  health,
  interactiveWords,
  onInteractiveWordsChange,
  onOptionsChange,
  onPreviewState,
  onMatteUrlReady,
  onJob,
}: {
  job: Job;
  start: number;
  end: number;
  aspect: "original" | "vertical";
  cropX: number;
  options: SubtleOptionsState;
  busy: boolean;
  refreshKey: number;
  health: Health | null;
  interactiveWords: boolean;
  onInteractiveWordsChange: (val: boolean) => void;
  onOptionsChange: (next: SubtleOptionsState) => void;
  onPreviewState: (state: {
    captions: CaptionsFile | null;
    style: Style | null;
    behindActive: boolean;
    meta: SubtlePreviewResponse | null;
  }) => void;
  onMatteUrlReady: (url: string | null) => void;
  onJob: (job: Job) => void;
}) {
  const [tab, setTab] = useState<StudioTab>("styles");
  const [catalog, setCatalog] = useState<SubtleStyleMeta[]>([]);
  const [segmentation, setSegmentation] = useState<SegmentationStatus | null>(health?.segmentation ?? null);
  const [renderer, setRenderer] = useState<RendererStatus | null>(health?.renderer ?? null);
  const [category, setCategory] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [needsAlign, setNeedsAlign] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [serverPreview, setServerPreview] = useState<SubtlePreviewResponse | null>(null);
  const [serverTweaksSnapshot, setServerTweaksSnapshot] = useState<SubtleOptionsState["tweaks"]>({});
  const [matteLoading, setMatteLoading] = useState(false);
  const [matteError, setMatteError] = useState<string | null>(null);
  const [recipeDraft, setRecipeDraft] = useState("");
  const [recipeError, setRecipeError] = useState<string | null>(null);
  const [replacementsDraft, setReplacementsDraft] = useState("");
  const reqSeq = useRef(0);

  // Sync catalog + segmentation + renderer info
  useEffect(() => {
    let alive = true;
    api<SubtleStylesResponse>("/subtle/styles")
      .then((res) => {
        if (!alive) return;
        setCatalog(res.styles || []);
        if (res.segmentation) setSegmentation(res.segmentation);
        if (res.renderer) setRenderer(res.renderer);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  // Poll MODNet download while active
  useEffect(() => {
    if (!segmentation || !["downloading", "verifying"].includes(segmentation.modnet_state)) return;
    let alive = true;
    const timer = setInterval(() => {
      api<SegmentationStatus>("/models/segmentation")
        .then((next) => {
          if (alive) setSegmentation(next);
        })
        .catch(() => {});
    }, 900);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [segmentation?.modnet_state]);

  // Clear matte preview when clip interval, crop, or matte quality changes
  useEffect(() => {
    onMatteUrlReady(null);
    setMatteError(null);
  }, [job.id, start, end, aspect, cropX, options.matte_quality, onMatteUrlReady]);

  // Debounced server preview fetch
  useEffect(() => {
    if (!job.id || !(end > start)) return;
    const mySeq = ++reqSeq.current;
    const timer = setTimeout(() => {
      setLoadingPreview(true);
      setPreviewError(null);
      api<SubtlePreviewResponse>(`/jobs/${job.id}/subtle-preview`, {
        method: "POST",
        body: JSON.stringify({
          start,
          end,
          aspect,
          crop_x: cropX,
          options,
        }),
      })
        .then((res) => {
          if (reqSeq.current !== mySeq) return;
          setNeedsAlign(false);
          setServerPreview(res);
          setServerTweaksSnapshot(options.tweaks);
        })
        .catch((err) => {
          if (reqSeq.current !== mySeq) return;
          const msg = err instanceof Error ? err.message : "Gagal menyiapkan pratinjau Subtle.";
          if (/waktu per kata/i.test(msg)) {
            setNeedsAlign(true);
            setServerPreview(null);
          } else {
            setPreviewError(msg);
          }
        })
        .finally(() => {
          if (reqSeq.current === mySeq) setLoadingPreview(false);
        });
    }, 240);
    return () => clearTimeout(timer);
  }, [job.id, start, end, aspect, cropX, options, refreshKey]);

  // Apply fast client-side visual tweaks while waiting for server refresh
  const liveOutput = useMemo(() => {
    if (!serverPreview) return { captions: null, style: null, behindActive: false };
    const baseCaptions = serverPreview.captions as unknown as CaptionsFile;
    const baseStyle = serverPreview.resolved_style as unknown as Style;
    const yNow = options.tweaks.y ?? undefined;
    const sizeNow = options.tweaks.size_pct ?? 100;
    const sizeThen = serverTweaksSnapshot.size_pct ?? 100;
    const relSizePct = sizeThen > 0 ? (sizeNow / sizeThen) * 100 : sizeNow;
    const clientTweak = {
      accent: options.tweaks.accent ?? undefined,
      y: yNow,
      case: options.tweaks.case ?? undefined,
      shadow: options.tweaks.shadow === false ? (false as const) : undefined,
      size: Math.abs(relSizePct - 100) > 0.1 ? relSizePct : undefined,
    };
    const over = buildOverride(baseStyle as Preset, clientTweak);
    const mergedStyle = Object.keys(over).length ? (deepMerge(baseStyle, over) as Style) : baseStyle;
    const preparedCaptions = prepareCaptions(
      baseCaptions,
      baseCaptions.video,
      {
        edits: Object.fromEntries(Object.entries(options.page_edits).map(([k, v]) => [Number(k), v])),
        highlights: options.emphasis !== "off",
        y: yNow,
      },
    );
    return {
      captions: preparedCaptions,
      style: mergedStyle,
      behindActive: serverPreview.behind_active,
    };
  }, [serverPreview, options.tweaks, options.page_edits, options.emphasis, serverTweaksSnapshot.size_pct]);

  useEffect(() => {
    onPreviewState({
      captions: liveOutput.captions,
      style: liveOutput.style,
      behindActive: liveOutput.behindActive,
      meta: serverPreview,
    });
  }, [liveOutput, serverPreview, onPreviewState]);

  const activeMeta = useMemo(
    () => catalog.find((s) => s.name === options.style) || null,
    [catalog, options.style],
  );

  const filteredStyles = useMemo(() => {
    const q = search.trim().toLowerCase();
    return catalog.filter((s) => {
      if (category !== "all" && s.category !== category) return false;
      if (q && !s.name.includes(q) && !s.label.toLowerCase().includes(q) && !s.description.toLowerCase().includes(q)) {
        return false;
      }
      return true;
    });
  }, [catalog, category, search]);

  const setOpt = useCallback(
    <K extends keyof SubtleOptionsState>(key: K, val: SubtleOptionsState[K]) => {
      onOptionsChange({ ...options, [key]: val });
    },
    [options, onOptionsChange],
  );

  const setTweak = useCallback(
    (patch: Partial<SubtleOptionsState["tweaks"]>) => {
      onOptionsChange({ ...options, tweaks: { ...options.tweaks, ...patch } });
    },
    [options, onOptionsChange],
  );

  const handleAlignWords = useCallback(async () => {
    try {
      setPreviewError(null);
      const next = await api<Job>(`/jobs/${job.id}/captions/align`, {
        method: "POST",
        body: JSON.stringify({ vocab: options.vocab, hinglish: options.hinglish }),
      });
      onJob(next);
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : "Gagal menyiapkan waktu kata.");
    }
  }, [job.id, options.vocab, options.hinglish, onJob]);

  const handleToggleWord = useCallback(
    (pageIndex: number, wordIndex: number, _text: string, currentlyEmphasized: boolean) => {
      onOptionsChange(toggleSubtleWordEmphasis(options, pageIndex, wordIndex, currentlyEmphasized));
    },
    [options, onOptionsChange],
  );

  const handleLoadMatte = useCallback(async () => {
    setMatteLoading(true);
    setMatteError(null);
    try {
      const res = await api<{ key: string; url: string }>(`/jobs/${job.id}/subtle-matte-preview`, {
        method: "POST",
        body: JSON.stringify({ start, end, aspect, crop_x: cropX, options }),
      });
      onMatteUrlReady(res.url);
    } catch (err) {
      setMatteError(err instanceof Error ? err.message : "Gagal membuat pratinjau matte.");
    } finally {
      setMatteLoading(false);
    }
  }, [job.id, start, end, aspect, cropX, options, onMatteUrlReady]);

  const handleDownloadModnet = useCallback(async () => {
    try {
      const st = await api<SegmentationStatus>("/models/segmentation/download", { method: "POST" });
      setSegmentation(st);
    } catch (err) {
      setMatteError(err instanceof Error ? err.message : "Gagal mengunduh MODNet.");
    }
  }, []);

  const handleApplyRecipe = useCallback(() => {
    if (!recipeDraft.trim()) {
      setRecipeError(null);
      setOpt("custom_recipe", null);
      return;
    }
    try {
      const parsed = JSON.parse(recipeDraft);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        setRecipeError("JSON recipe harus berupa objek.");
        return;
      }
      setRecipeError(null);
      setOpt("custom_recipe", parsed as Record<string, unknown>);
    } catch (err) {
      setRecipeError(err instanceof Error ? err.message : "JSON recipe tidak valid.");
    }
  }, [recipeDraft, setOpt]);

  const applyReplacementsText = useCallback(
    (raw: string) => {
      setReplacementsDraft(raw);
      const map: Record<string, string> = {};
      for (const line of raw.split(/\r?\n/)) {
        const idx = line.indexOf("=");
        if (idx > 0) {
          const k = line.slice(0, idx).trim();
          const v = line.slice(idx + 1).trim();
          if (k && v) map[k] = v;
        }
      }
      setOpt("replacements", map);
    },
    [setOpt],
  );

  const modnetMb = segmentation ? (segmentation.modnet_total_bytes / 1_000_000).toFixed(1) : "25.9";
  const pages = liveOutput.captions?.pages ?? [];

  return (
    <section className={styles.panel} aria-label="Studio Subtle Captions">
      <div className={styles.header}>
        <div className={styles.title}>
          <Sparkles size={15} />
          <span>Subtle Motion Studio</span>
          {loadingPreview && <LoaderCircle size={13} className="spin" />}
        </div>
        <span className={styles.badge}>
          {options.custom_recipe ? "Custom Recipe" : `${options.style}${options.variant ? `:${options.variant}` : ""}`}
        </span>
      </div>

      {renderer && !renderer.available && (
        <div className={`${styles.statusCard} ${styles.warnCard}`}>
          <strong>Renderer ekspor Remotion belum siap</strong>
          <span>{renderer.reason || "Jalankan scripts\\setup.ps1 untuk membangun bundle renderer."}</span>
        </div>
      )}

      {needsAlign && (
        <div className={`${styles.statusCard} ${styles.warnCard}`}>
          <strong>Waktu kata asli belum tersedia untuk Subtle</strong>
          <span>Klik tombol di bawah agar Whisper membuat timestamp per kata terlebih dahulu.</span>
          <div className={styles.actionsRow}>
            <button type="button" className="button primary small" disabled={busy} onClick={() => void handleAlignWords()}>
              <Wand2 size={14} /> Siapkan waktu per kata
            </button>
          </div>
        </div>
      )}

      {previewError && (
        <div className={`${styles.statusCard} ${styles.warnCard}`} role="alert">
          <span>{previewError}</span>
        </div>
      )}

      <div className={styles.tabs} role="tablist" aria-label="Pengaturan Subtle">
        <button type="button" role="tab" aria-selected={tab === "styles"} className={`${styles.tabBtn} ${tab === "styles" ? styles.tabActive : ""}`} onClick={() => setTab("styles")}>
          <Palette size={13} /> Gaya
        </button>
        <button type="button" role="tab" aria-selected={tab === "smart"} className={`${styles.tabBtn} ${tab === "smart" ? styles.tabActive : ""}`} onClick={() => setTab("smart")}>
          <Sliders size={13} /> Cerdas
        </button>
        <button type="button" role="tab" aria-selected={tab === "vision"} className={`${styles.tabBtn} ${tab === "vision" ? styles.tabActive : ""}`} onClick={() => setTab("vision")}>
          <UserCheck size={13} /> Visi
        </button>
        <button type="button" role="tab" aria-selected={tab === "recipe"} className={`${styles.tabBtn} ${tab === "recipe" ? styles.tabActive : ""}`} onClick={() => setTab("recipe")}>
          <Music size={13} /> QA & JSON
        </button>
      </div>

      {tab === "styles" && (
        <>
          <div className={styles.filterRow}>
            <input
              type="search"
              className={styles.searchInput}
              placeholder="Cari 39 preset (glide, glass, keyword, ticket, downbeat...)"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <div className={styles.catRow}>
              {CATEGORIES.map((c) => {
                const count = c.id === "all" ? catalog.length : catalog.filter((s) => s.category === c.id).length;
                return (
                  <button
                    key={c.id}
                    type="button"
                    className={`${styles.catChip} ${category === c.id ? styles.catChipActive : ""}`}
                    onClick={() => setCategory(c.id)}
                  >
                    {c.label} ({count})
                  </button>
                );
              })}
            </div>
          </div>

          <div className={styles.styleGrid}>
            {filteredStyles.map((item) => {
              const selected = options.style === item.name && !options.custom_recipe;
              return (
                <button
                  key={item.name}
                  type="button"
                  className={`${styles.styleCard} ${selected ? styles.styleCardActive : ""}`}
                  disabled={busy}
                  onClick={() => onOptionsChange({ ...options, style: item.name, variant: "", custom_recipe: null })}
                >
                  <div className={styles.styleCardTop}>
                    <strong>{item.label}</strong>
                    <span className={styles.miniTag}>{item.category}</span>
                  </div>
                  <p>{item.description}</p>
                  <div className={styles.miniTags}>
                    {item.behind_enabled && <span className={styles.miniTag}>behind</span>}
                    {item.backdrop_blur && <span className={styles.miniTag}>blur</span>}
                    {item.group === "music" && <span className={styles.miniTag}>beat</span>}
                    {item.variants.length > 0 && <span className={styles.miniTag}>{item.variants.length} varian</span>}
                  </div>
                </button>
              );
            })}
          </div>

          {activeMeta && activeMeta.variants.length > 0 && (
            <div className={styles.actionsRow}>
              <span style={{ fontSize: 10, color: "#9ba992" }}>Varian warna:</span>
              <button
                type="button"
                className={`${styles.pillBtn} ${options.variant === "" ? styles.pillBtnActive : ""}`}
                onClick={() => setOpt("variant", "")}
              >
                Default
              </button>
              {activeMeta.variants.map((v) => (
                <button
                  key={v}
                  type="button"
                  className={`${styles.pillBtn} ${options.variant === v ? styles.pillBtnActive : ""}`}
                  onClick={() => setOpt("variant", v)}
                >
                  {v}
                </button>
              ))}
            </div>
          )}

          <div className={styles.fieldGrid}>
            <label className={styles.field}>
              <span className={styles.fieldHeader}>
                <span>Ukuran teks</span>
                <strong>{Math.round(options.tweaks.size_pct ?? 100)}%</strong>
              </span>
              <input
                type="range"
                min={50}
                max={180}
                step={2}
                value={options.tweaks.size_pct ?? 100}
                disabled={busy}
                onChange={(e) => setTweak({ size_pct: Number(e.target.value) })}
              />
            </label>

            <label className={styles.field}>
              <span className={styles.fieldHeader}>
                <span>Posisi vertikal (Y)</span>
                <strong>{options.tweaks.y != null ? `${Math.round(options.tweaks.y)}%` : "Auto"}</strong>
              </span>
              <div className={styles.colorRow}>
                <input
                  type="range"
                  min={10}
                  max={88}
                  step={1}
                  value={options.tweaks.y ?? activeMeta?.default_y ?? 72}
                  disabled={busy}
                  onChange={(e) => setTweak({ y: Number(e.target.value) })}
                />
                {options.tweaks.y != null && (
                  <button type="button" className={styles.pillBtn} onClick={() => setTweak({ y: null })} title="Kembali ke Smart Placement">
                    <RotateCcw size={11} />
                  </button>
                )}
              </div>
            </label>

            <label className={styles.field}>
              <span>Warna aksen</span>
              <div className={styles.colorRow}>
                <input
                  type="color"
                  className={styles.colorInput}
                  value={options.tweaks.accent || activeMeta?.default_accent || "#D4F768"}
                  disabled={busy}
                  onChange={(e) => setTweak({ accent: e.target.value.toUpperCase() })}
                />
                <button type="button" className={styles.pillBtn} onClick={() => setTweak({ accent: null })}>
                  Auto palet
                </button>
              </div>
            </label>

            <label className={styles.field}>
              <span>Huruf (Case)</span>
              <select
                className={styles.select}
                value={options.tweaks.case || ""}
                disabled={busy}
                onChange={(e) => setTweak({ case: (e.target.value || null) as SubtleOptionsState["tweaks"]["case"] })}
              >
                <option value="">Default preset</option>
                <option value="upper">UPPERCASE</option>
                <option value="sentence">Sentence case</option>
                <option value="lower">lowercase</option>
              </select>
            </label>

            <label className={styles.field}>
              <span>Maks kata / halaman</span>
              <select
                className={styles.select}
                value={options.max_words ?? ""}
                disabled={busy}
                onChange={(e) => setOpt("max_words", e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">Otomatis gaya</option>
                {[1, 2, 3, 4, 5, 6].map((n) => (
                  <option key={n} value={n}>{n} kata</option>
                ))}
              </select>
            </label>

            <label className={styles.field}>
              <span>Bayangan & Halo keterbacaan</span>
              <button
                type="button"
                className={`${styles.pillBtn} ${options.tweaks.shadow !== false ? styles.pillBtnActive : ""}`}
                onClick={() => setTweak({ shadow: options.tweaks.shadow === false ? true : false })}
              >
                {options.tweaks.shadow === false ? "Dimatikan (Flat)" : "Aktif (WCAG Auto)"}
              </button>
            </label>
          </div>
        </>
      )}

      {tab === "smart" && (
        <>
          <div className={styles.fieldGrid}>
            <label className={styles.field}>
              <span>Mode Penekanan Kata</span>
              <select
                className={styles.select}
                value={options.emphasis}
                disabled={busy}
                onChange={(e) => setOpt("emphasis", e.target.value as SubtleOptionsState["emphasis"])}
              >
                <option value="auto">Otomatis (Angka, nama, kontras)</option>
                <option value="numbers">Hanya angka & harga</option>
                <option value="off">Tanpa penekanan</option>
              </select>
            </label>

            <label className={styles.field}>
              <span>Normalisasi Angka Terucap</span>
              <select
                className={styles.select}
                value={options.numbers}
                disabled={busy}
                onChange={(e) => setOpt("numbers", e.target.value as SubtleOptionsState["numbers"])}
              >
                <option value="auto">Auto (EN/HI/ES/ID)</option>
                <option value="indonesian">Indonesia (Rp, juta, ribu)</option>
                <option value="western">Barat (ribuan koma)</option>
                <option value="indian">India (lakh, crore, ₹)</option>
                <option value="european">Eropa (titik/koma)</option>
                <option value="off">Biarkan teks asli</option>
              </select>
            </label>

            <label className={`${styles.field} ${styles.fieldWide}`}>
              <span>Kata wajib di-highlight (pisahkan dengan koma)</span>
              <input
                type="text"
                className={styles.textInput}
                placeholder="misal: Rp500rb, gratis, rahasia"
                value={options.emphasis_words.join(", ")}
                disabled={busy}
                onChange={(e) =>
                  setOpt(
                    "emphasis_words",
                    e.target.value.split(",").map((x) => x.trim()).filter(Boolean),
                  )
                }
              />
            </label>

            <label className={`${styles.field} ${styles.fieldWide}`}>
              <span>Kosakata khusus Whisper & re-align (nama brand/istilah)</span>
              <div className={styles.colorRow}>
                <input
                  type="text"
                  className={styles.textInput}
                  placeholder="misal: ClipClip, Podcut, Hinglish"
                  value={options.vocab}
                  disabled={busy}
                  onChange={(e) => setOpt("vocab", e.target.value)}
                />
                <button type="button" className="button secondary small" disabled={busy} onClick={() => void handleAlignWords()}>
                  Re-align
                </button>
              </div>
            </label>
          </div>

          <label className={styles.toggleRow}>
            <span>Bersihkan kata pengisi (um, uh, ehm, hmm)</span>
            <input type="checkbox" checked={options.clean_fillers} onChange={(e) => setOpt("clean_fillers", e.target.checked)} />
          </label>
          <label className={styles.toggleRow}>
            <span>Transliterasi Hindi ke Latin (Hinglish)</span>
            <input type="checkbox" checked={options.hinglish} onChange={(e) => setOpt("hinglish", e.target.checked)} />
          </label>
          <label className={styles.toggleRow}>
            <span>Hapus highlight otomatis (hanya klik manual)</span>
            <input
              type="checkbox"
              checked={options.clear_auto_emphasis}
              onChange={(e) => setOpt("clear_auto_emphasis", e.target.checked)}
            />
          </label>

          <label className={styles.field}>
            <span>Penggantian kata (satu per baris: salah=benar)</span>
            <textarea
              className={styles.textarea}
              placeholder={"klipklip=ClipClip\ngoceng=Rp5.000"}
              value={replacementsDraft}
              onChange={(e) => applyReplacementsText(e.target.value)}
            />
          </label>

          <div className={styles.actionsRow}>
            <button
              type="button"
              className={`${styles.pillBtn} ${interactiveWords ? styles.pillBtnActive : ""}`}
              onClick={() => onInteractiveWordsChange(!interactiveWords)}
            >
              <MousePointerClick size={12} /> {interactiveWords ? "Mode klik kata aktif di video" : "Klik kata langsung di video"}
            </button>
            {(options.manual_picks.length > 0 || options.manual_drops.length > 0) && (
              <button
                type="button"
                className={styles.pillBtn}
                onClick={() => onOptionsChange({ ...options, manual_picks: [], manual_drops: [] })}
              >
                Reset klik manual ({options.manual_picks.length + options.manual_drops.length})
              </button>
            )}
          </div>

          {pages.length > 0 && (
            <div className={styles.wordPages}>
              {pages.map((p, pi) => {
                const defaultLine = p.words.map((w) => w.text).join(" ");
                return (
                  <div className={styles.pageBox} key={pi}>
                    <div className={styles.pageBoxMeta}>
                      <span>Halaman #{pi + 1} · {(p.startMs / 1000).toFixed(2)}s</span>
                      {p.y != null && <span>Y={Math.round(p.y)}%</span>}
                    </div>
                    <div className={styles.wordChips}>
                      {p.words.map((w, wi) => (
                        <button
                          key={wi}
                          type="button"
                          className={`${styles.wordChip} ${w.emphasis ? styles.wordChipEmph : ""}`}
                          title={(w as { reason?: string }).reason || "Klik untuk toggle highlight"}
                          onClick={() => handleToggleWord(pi, wi, w.text, Boolean(w.emphasis))}
                        >
                          {w.text}
                        </button>
                      ))}
                    </div>
                    <input
                      type="text"
                      className={styles.textInput}
                      value={options.page_edits[String(pi)] ?? defaultLine}
                      aria-label={`Sunting teks halaman ${pi + 1}`}
                      onChange={(e) => {
                        const val = e.target.value;
                        const next = { ...options.page_edits };
                        if (!val || val === defaultLine) delete next[String(pi)];
                        else next[String(pi)] = val;
                        setOpt("page_edits", next);
                      }}
                    />
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}

      {tab === "vision" && (
        <>
          <div className={styles.fieldGrid}>
            <label className={styles.field}>
              <span>Smart Placement (anti wajah)</span>
              <select className={styles.select} value={options.placement} onChange={(e) => setOpt("placement", e.target.value as "auto" | "off")}>
                <option value="auto">Auto (ONNX)</option>
                <option value="off">Nonaktif</option>
              </select>
            </label>

            <label className={styles.field}>
              <span>Pindah Halaman di Hard-Cut</span>
              <select className={styles.select} value={options.cuts} onChange={(e) => setOpt("cuts", e.target.value as "auto" | "off")}>
                <option value="auto">Auto</option>
                <option value="off">Nonaktif</option>
              </select>
            </label>

            <label className={styles.field}>
              <span>Auto Legibility (WCAG)</span>
              <select className={styles.select} value={options.legibility} onChange={(e) => setOpt("legibility", e.target.value as "auto" | "off")}>
                <option value="auto">Auto halo/scrim</option>
                <option value="off">Nonaktif</option>
              </select>
            </label>

            <label className={styles.field}>
              <span>Auto Palette dari Video</span>
              <select className={styles.select} value={options.palette} onChange={(e) => setOpt("palette", e.target.value as "auto" | "off")}>
                <option value="auto">Auto warna dominan</option>
                <option value="off">Gunakan default/manual</option>
              </select>
            </label>

            <label className={styles.field}>
              <span>Teks Besar di Belakang Subjek</span>
              <select className={styles.select} value={options.behind} onChange={(e) => setOpt("behind", e.target.value as "auto" | "on" | "off")}>
                <option value="auto">Auto (gaya behind)</option>
                <option value="on">Selalu aktifkan</option>
                <option value="off">Matikan behind</option>
              </select>
            </label>

            <label className={styles.field}>
              <span>Kualitas Cutout ONNX</span>
              <select className={styles.select} value={options.matte_quality} onChange={(e) => setOpt("matte_quality", e.target.value as "fast" | "balanced" | "accurate")}>
                <option value="fast">Fast (ringan)</option>
                <option value="balanced">Balanced</option>
                <option value="accurate">Accurate (MODNet jika siap)</option>
              </select>
            </label>
          </div>

          {segmentation && (
            <div className={styles.statusCard}>
              <strong>Segmentasi Subjek Windows (Apache-2.0 ONNX)</strong>
              <span>{segmentation.message}</span>
              <div className={styles.actionsRow}>
                {segmentation.modnet_state === "downloading" || segmentation.modnet_state === "verifying" ? (
                  <>
                    <LoaderCircle size={13} className="spin" />
                    <span>{segmentation.modnet_percent.toFixed(0)}%</span>
                    <button
                      type="button"
                      className={styles.pillBtn}
                      onClick={() => void api<SegmentationStatus>("/models/segmentation/cancel", { method: "POST" }).then(setSegmentation)}
                    >
                      Batalkan
                    </button>
                  </>
                ) : segmentation.modnet_ready ? (
                  <span style={{ color: "var(--lime)", display: "flex", alignItems: "center", gap: 4 }}>
                    <CheckCheck size={13} /> Tier-2 MODNet Akurat terpasang
                  </span>
                ) : (
                  <button type="button" className={styles.pillBtn} onClick={() => void handleDownloadModnet()}>
                    <Download size={12} /> Unduh MODNet Akurat ({modnetMb} MB)
                  </button>
                )}

                {!job.media?.audio_only && (
                  <button type="button" className={styles.pillBtn} disabled={matteLoading || busy} onClick={() => void handleLoadMatte()}>
                    {matteLoading ? <LoaderCircle size={12} className="spin" /> : <Eye size={12} />} Pratinjau Cutout Live
                  </button>
                )}
              </div>
              {matteError && <span style={{ color: "#e8a888" }}>{matteError}</span>}
            </div>
          )}

          <div className={styles.fieldGrid}>
            <label className={`${styles.field} ${styles.fieldWide}`}>
              <span>Lirik Musik (opsional, diselaraskan ke waktu ucapan & ketukan)</span>
              <textarea
                className={styles.textarea}
                placeholder="Tempel lirik lagu di sini untuk gaya downbeat / billboard / chalkline / singalong..."
                value={options.lyrics}
                onChange={(e) => setOpt("lyrics", e.target.value)}
              />
            </label>

            {(job.media?.audio_only || activeMeta?.group === "music") && (
              <>
                <label className={styles.field}>
                  <span>Kanvas Audio</span>
                  <select className={styles.select} value={options.canvas} onChange={(e) => setOpt("canvas", e.target.value as SubtleOptionsState["canvas"])}>
                    <option value="9:16">9:16 Vertikal (1080×1920)</option>
                    <option value="16:9">16:9 Lanskap (1920×1080)</option>
                    <option value="1:1">1:1 Persegi (1080×1080)</option>
                    <option value="4:5">4:5 Feed (1080×1350)</option>
                  </select>
                </label>

                <label className={styles.field}>
                  <span>Latar Kanvas Audio</span>
                  <div className={styles.colorRow}>
                    <input
                      type="color"
                      className={styles.colorInput}
                      value={options.canvas_bg}
                      onChange={(e) => setOpt("canvas_bg", e.target.value.toUpperCase())}
                    />
                    <span style={{ fontFamily: "Consolas, monospace", fontSize: 10 }}>{options.canvas_bg}</span>
                  </div>
                </label>
              </>
            )}
          </div>
        </>
      )}

      {tab === "recipe" && (
        <>
          {serverPreview && (
            <div className={styles.statusCard}>
              <strong>Diagnostik Pipeline Subtle</strong>
              <div className={styles.diagRow}>
                {serverPreview.palette?.accent && (
                  <span className={styles.diagSwatch}>
                    <span className={styles.swatchDot} style={{ background: String(serverPreview.palette.accent) }} />
                    Aksen {String(serverPreview.palette.accent)} ({String(serverPreview.palette.contrast ?? "—")}:1)
                  </span>
                )}
                {serverPreview.beats?.bpm && (
                  <span className={styles.diagSwatch}>{serverPreview.beats.bpm} BPM</span>
                )}
                {serverPreview.cuts.length > 0 && (
                  <span className={styles.diagSwatch}>{serverPreview.cuts.length} cut</span>
                )}
                {serverPreview.behind_active && (
                  <span className={styles.diagSwatch}>Behind aktif</span>
                )}
              </div>
              {serverPreview.notes.length > 0 && (
                <ul className={styles.notesList}>
                  {serverPreview.notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <label className={styles.field}>
            <span>Custom JSON Recipe Style (opsional)</span>
            <textarea
              className={`${styles.textarea} ${styles.monoArea}`}
              placeholder={'{\n  "name": "recipe",\n  "anim": { "type": "recipe", "words": "slide" },\n  "font": { "family": "Inter Bold", "file": "Inter-Bold.ttf", "weight": 700 }\n}'}
              value={recipeDraft}
              onChange={(e) => setRecipeDraft(e.target.value)}
            />
          </label>
          {recipeError && <p className={styles.hint} style={{ color: "#e8a888" }}>{recipeError}</p>}
          <div className={styles.actionsRow}>
            <button type="button" className={styles.pillBtn} onClick={handleApplyRecipe}>
              Terapkan JSON Recipe
            </button>
            {options.custom_recipe && (
              <button
                type="button"
                className={styles.pillBtn}
                onClick={() => {
                  setRecipeDraft("");
                  setRecipeError(null);
                  setOpt("custom_recipe", null);
                }}
              >
                Kembali ke Preset
              </button>
            )}
          </div>
          <p className={styles.hint}>Font pada custom recipe akan divalidasi hanya dari koleksi Font OFL lokal bawaan.</p>
        </>
      )}
    </section>
  );
}
