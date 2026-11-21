"use client";

import { useEffect, useState, type RefObject } from "react";
import { api } from "@/lib/api";
import type { Job } from "@/lib/types";

export type SubtitleStyle = { font_weight: 600 | 700; font_size_pct: number; position_y_pct: number; active_color: string; stroke_px: number; shadow_px: number; max_words: number };
export const defaultSubtitleStyle: SubtitleStyle = { font_weight: 600, font_size_pct: 4.5, position_y_pct: 70, active_color: "#D7F35C", stroke_px: 1, shadow_px: .5, max_words: 4 };
export type CaptionDocument = { version: string; start: number; end: number; duration: number; width: number; height: number; style: SubtitleStyle; words: { id: number; start: number; end: number; text: string }[]; groups: { id: number; word_ids: number[]; lines: number[][]; start: number; end: number }[]; warnings: string[]; render_info?:{font_size_px:number;ass_font_size_px:number;line_height:number} };

export function PoppinsOverlay({ videoRef, document, enabled }: { videoRef: RefObject<HTMLVideoElement | null>; document: CaptionDocument | null; enabled: boolean }) {
  const [clock, setClock] = useState(0);
  const [width, setWidth] = useState(0);
  const [fontsReady, setFontsReady] = useState(false);
  useEffect(() => {
    let valid = true;
    void Promise.all([window.document.fonts.load('600 24px "Poppins"'), window.document.fonts.load('700 24px "Poppins"')]).then(() => { if (valid) setFontsReady(true); });
    return () => { valid = false; };
  }, []);
  useEffect(() => {
    if (!enabled || !document) return;
    let frame = 0;
    const tick = () => {
      const player = videoRef.current;
      if (player) { setClock(player.currentTime - document.start); setWidth(player.getBoundingClientRect().width); }
      frame = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(frame);
  }, [document, enabled, videoRef]);
  if (!enabled || !document || !fontsReady || width <= 0) return null;
  const group = document.groups.find(item => clock >= item.start && clock < item.end);
  if (!group) return null;
  const lookup = new Map(document.words.map(word => [word.id, word]));
  const scale = width / document.width;
  const size = (document.render_info?.font_size_px ?? Math.min(document.width, document.height) * document.style.font_size_pct / 100) * scale;
  const active = document.words.filter(word => group.word_ids.includes(word.id) && clock >= word.start && clock < word.end).sort((a, b) => b.start - a.start || b.id - a.id)[0]?.id;
  return <div className="poppins-caption-overlay" aria-label="Pratinjau subtitle Poppins" style={{ top: `${document.style.position_y_pct}%`, fontSize: size, fontWeight: document.style.font_weight, WebkitTextStroke: `${document.style.stroke_px * scale}px rgba(0,0,0,.85)`, textShadow: `${document.style.shadow_px * scale}px ${document.style.shadow_px * scale}px ${Math.max(.5, document.style.shadow_px * scale)}px #000` }}>
    {group.lines.map((line, index) => <div className="poppins-caption-line" key={index}>{line.map((id, wordIndex) => { const word = lookup.get(id); const previous = lookup.get(line[wordIndex - 1])?.text || ""; const separator = !previous || /^[,.;:!?%\)\]\}”’]/.test(word?.text || "") || /[\(\[\{“‘]$/.test(previous) ? "" : " "; return word ? <span key={id} data-word-id={id} data-active={id === active ? "true" : "false"} style={{ color: id === active ? document.style.active_color : "#FFFFFF" }}>{separator}{word.text}</span> : null; })}</div>)}
  </div>;
}

export function SubtitlePanel({ job, start, end, aspect, style, enabled, busy, refreshKey, onStyle, onDocument, onJob }: { job: Job; start: number; end: number; aspect: "original" | "vertical"; style: SubtitleStyle; enabled: boolean; busy: boolean; refreshKey: number; onStyle: (style: SubtitleStyle) => void; onDocument: (document: CaptionDocument | null) => void; onJob: (job: Job) => void }) {
  const [document, setDocument] = useState<CaptionDocument | null>(null);
  const [edits, setEdits] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [revision, setRevision] = useState(0);
  const styleKey = JSON.stringify(style);
  useEffect(() => {
    const controller = new AbortController();
    onDocument(null); setDocument(null); setEdits({});
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start || !job.media || end > job.media.duration + .01 || end - start > 600 || busy) return;
    const timer = setTimeout(() => {
      void api<CaptionDocument>(`/jobs/${job.id}/caption-preview`, { method: "POST", body: JSON.stringify({ start, end, aspect, style: JSON.parse(styleKey) }), signal: controller.signal }).then(next => {
        if (!controller.signal.aborted) { setDocument(next); onDocument(next); setError(null); }
      }).catch(err => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Subtitle belum tersedia."); });
    }, 180);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [job.id, job.status, job.transcript_ready, start, end, aspect, styleKey, revision, refreshKey, busy, onDocument]);

  async function saveWords() {
    setWorking(true); setError(null);
    try {
      const changes = Object.entries(edits).map(([word_id, text]) => ({ word_id: Number(word_id), text: text.trim() }));
      if (changes.some(word => !word.text || /\s/.test(word.text))) throw new Error("Ubah satu kata per kotak. Waktu ucapan tetap dipertahankan.");
      await api(`/jobs/${job.id}/caption-words`, { method: "PATCH", body: JSON.stringify({ changes }) });
      setRevision(old => old + 1); setEdits({});
    } catch (err) { setError(err instanceof Error ? err.message : "Koreksi belum tersimpan."); }
    finally { setWorking(false); }
  }

  async function align() {
    setWorking(true); setError(null);
    try { onJob(await api<Job>(`/jobs/${job.id}/captions/align`, { method: "POST" })); }
    catch (err) { setError(err instanceof Error ? err.message : "Penyelarasan belum dapat dimulai."); }
    finally { setWorking(false); }
  }

  return <section className="subtitle-panel" aria-label="Editor subtitle Poppins">
    <div className="section-heading"><span>Subtitle Poppins Highlight</span><span className="micro">PUTIH + SATU KATA LIME</span></div>
    <p className="subtitle-help">Sinkron per kata, tanpa kotak latar. Koreksi teks tidak mengubah dialog, waktu ucapan, atau analisis klip.</p>
    {enabled && <div className="subtitle-style-grid">
      <label>Ketebalan<select aria-label="Ketebalan Poppins" value={style.font_weight} onChange={e => onStyle({ ...style, font_weight: Number(e.target.value) as 600 | 700 })} disabled={busy}><option value="600">Poppins SemiBold · 600</option><option value="700">Poppins Bold · 700</option></select></label>
      <label>Warna kata aktif<input type="color" aria-label="Warna kata aktif" value={style.active_color} onChange={e => onStyle({ ...style, active_color: e.target.value })} disabled={busy} /></label>
      <label>Ukuran teks <strong>{style.font_size_pct.toFixed(1)}%</strong><input aria-label="Ukuran subtitle" type="range" min="2" max="9" step=".25" value={style.font_size_pct} onChange={e => onStyle({ ...style, font_size_pct: Number(e.target.value) })} disabled={busy} /></label>
      <label>Posisi vertikal <strong>{style.position_y_pct}%</strong><input aria-label="Posisi subtitle" type="range" min="20" max="85" step="1" value={style.position_y_pct} onChange={e => onStyle({ ...style, position_y_pct: Number(e.target.value) })} disabled={busy} /></label>
    </div>}
    {error && <p className="field-error" role="status">{error}</p>}
    {document?.warnings.map((warning, index) => <p className="subtitle-help" key={index}>{warning}</p>)}
    {!document?.words.length && <button className="button secondary small" onClick={() => void align()} disabled={working || busy}>Siapkan waktu per kata</button>}
    {document && document.words.length > 0 && <details className="subtitle-corrections"><summary>Koreksi teks subtitle <span>{document.words.length} kata</span></summary><div className="subtitle-word-editor">{document.words.map(word => <label key={word.id} title={`${word.start.toFixed(2)}–${word.end.toFixed(2)} detik`}><span>{word.start.toFixed(2)}s</span><input aria-label={`Koreksi kata ${word.id}`} value={edits[word.id] ?? word.text} maxLength={80} style={{ width: `${Math.max(4, Math.min(18, (edits[word.id] ?? word.text).length + 1))}ch` }} onChange={e => setEdits(old => ({ ...old, [word.id]: e.target.value }))} disabled={working || busy} /></label>)}</div><button className="button secondary small" disabled={working || busy || !Object.keys(edits).length} onClick={() => void saveWords()}>Simpan koreksi kata</button></details>}
  </section>;
}
