"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCheck, Download, HardDrive, LoaderCircle, UploadCloud } from "lucide-react";
import { api, upload } from "@/lib/api";
import styles from "./ModelSetup.module.css";

type ModelState = {
  state: "missing" | "verifying" | "downloading" | "copying" | "ready" | "failed" | "cancelled";
  model: string; revision: string; location: string; message: string;
  total_bytes: number; completed_bytes: number; verified_bytes: number;
  received_bytes: number; speed_bps: number | null; percent: number;
  filename: string | null; attempt: number; operation: string | null;
};

function bytes(value: number) {
  return `${(value / 1_000_000).toLocaleString("id-ID", { maximumFractionDigits: 1 })} MB`;
}

export function ModelSetup({ onReady }: { onReady: () => void }) {
  const [model, setModel] = useState<ModelState | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [uploadPercent, setUploadPercent] = useState<number | null>(null);
  const autoStarted = useRef(false);
  const previousState = useRef<string | undefined>(undefined);
  const offlineInput = useRef<HTMLInputElement>(null);
  const working = model && ["downloading", "copying", "verifying"].includes(model.state);

  useEffect(() => {
    let stopped = false;
    const poll = async () => {
      try {
        const next = await api<ModelState>("/models/whisper");
        if (stopped) return;
        setModel(next);
        if (next.state === "ready" && previousState.current && previousState.current !== "ready") onReady();
        previousState.current = next.state;
      } catch (reason) {
        if (!stopped) setError(reason instanceof Error ? reason.message : "Status model belum dapat diperiksa.");
      }
    };
    void poll();
    const timer = setInterval(() => void poll(), 1500);
    return () => { stopped = true; clearInterval(timer); };
  }, [onReady]);

  const download = useCallback(async () => {
    setPending(true); setError("");
    try { setModel(await api<ModelState>("/models/whisper/download", { method: "POST" })); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Setup model belum dapat dimulai."); }
    finally { setPending(false); }
  }, []);

  useEffect(() => {
    if (model?.state !== "missing" || autoStarted.current) return;
    // Render source/size/location first. A failed transfer requires explicit retry.
    const timer = setTimeout(() => { autoStarted.current = true; void download(); }, 700);
    return () => clearTimeout(timer);
  }, [model?.state, download]);

  async function importOffline(file: File) {
    setPending(true); setError(""); setUploadPercent(0);
    try { setModel(await upload<ModelState>("/models/whisper/offline", file, setUploadPercent)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Paket offline belum dapat diimpor."); }
    finally { setPending(false); setUploadPercent(null); }
  }

  if (!model && !error) return null;
  if (!model) return <section className={styles.panel} role="alert"><strong>Setup model lokal</strong><p>{error}</p></section>;

  if (model.state === "ready") return <details className={styles.ready}>
    <summary><CheckCheck size={16} /> Model Whisper lokal siap <span>Tanpa API key</span></summary>
    <p>Whisper {model.model} · {bytes(model.total_bytes)} · checksum dan versi terverifikasi.</p>
    <p className={styles.path}>{model.location}</p>
    <a className="text-button" href="/api/models/whisper/offline-package" download><Download size={14} />Simpan paket model offline untuk PC lain</a>
    <p>Paket ZIP sekitar {bytes(model.total_bytes)}. Simpan untuk setup PC tanpa internet.</p>
  </details>;

  return <section className={styles.panel} aria-label="Setup model transkripsi lokal">
    <div className={styles.heading}><div className={styles.symbol}><HardDrive size={21} /></div><div><h2>Menyiapkan transkripsi lokal</h2><p>Satu kali setup. Video dan analisis heuristik tetap lokal; API key tidak diperlukan.</p></div></div>
    <dl className={styles.information}><div><dt>Model publik</dt><dd>Whisper {model.model} · MIT</dd></div><div><dt>Ukuran model</dt><dd>{bytes(model.total_bytes)}</dd></div><div><dt>Lokasi penyimpanan</dt><dd className={styles.path}>{model.location}</dd></div></dl>
    <p className={styles.message} role="status">{working && <LoaderCircle size={15} className="spin" />}{model.message}</p>
    <progress className={styles.progress} value={Math.max(0, Math.min(100, model.percent))} max={100} aria-label="Progress setup model" />
    <div className={styles.stats}><span>{model.percent.toLocaleString("id-ID", { maximumFractionDigits: 1 })}% · {bytes(model.completed_bytes)} / {bytes(model.total_bytes)}</span><span>{model.speed_bps === null ? "" : `${bytes(model.speed_bps)}/dtk`}</span></div>
    {!!model.received_bytes && <p className={styles.note}>{model.operation === "offline" ? "Disalin dari ZIP" : "Diterima lewat internet"}: {bytes(model.received_bytes)}. File yang sudah valid digunakan kembali.</p>}
    {uploadPercent !== null && <p role="status">Mengirim paket dari komputer ini: {uploadPercent}%</p>}
    {error && <p className={styles.error} role="alert">{error}</p>}
    <div className={styles.actions}>
      {!working && <button className="button primary" onClick={() => void download()} disabled={pending}><Download size={16} />{model.state === "missing" ? "Unduh model" : "Coba unduh lagi"}</button>}
      {working && <button className="button secondary" onClick={() => void api<ModelState>("/models/whisper/cancel", { method: "POST" }).then(setModel).catch(reason => setError(reason.message))}>Batalkan setup</button>}
      <button className="button secondary" disabled={!!working || pending} onClick={() => offlineInput.current?.click()}><UploadCloud size={16} />Impor paket offline ZIP</button>
      <input ref={offlineInput} type="file" accept=".zip,application/zip" hidden onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void importOffline(file); }} />
    </div>
    <p className={styles.note}>Tidak ada internet? Gunakan ZIP dari tombol “Simpan paket model offline” pada ClipClip.ai yang modelnya sudah siap. Ukuran, versi, dan SHA256 setiap file diperiksa sebelum transkripsi diaktifkan.</p>
  </section>;
}
