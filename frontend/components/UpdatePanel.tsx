"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCheck, Download, LoaderCircle, RefreshCw, ShieldCheck, X } from "lucide-react";
import { api } from "@/lib/api";
import type { UpdateStatus } from "@/lib/types";
import styles from "./UpdatePanel.module.css";

function formatBytes(value: number) {
  return `${(value / 1_048_576).toLocaleString("id-ID", { maximumFractionDigits: 1 })} MB`;
}

export function UpdatePanel({ initial }: { initial?: UpdateStatus | null }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<UpdateStatus | null>(initial ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const autoChecked = useRef(false);

  useEffect(() => {
    if (initial) setStatus(prev => prev ?? initial);
  }, [initial]);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api<UpdateStatus>("/updates"));
    } catch {
      /* Connection banner handles unreachable local service. */
    }
  }, []);

  const checkNow = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      setStatus(await api<UpdateStatus>("/updates/check", { method: "POST" }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Pemeriksaan update gagal.");
    } finally {
      setBusy(false);
    }
  }, []);

  const downloadNow = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      setStatus(await api<UpdateStatus>("/updates/download", { method: "POST" }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unduhan update gagal dimulai.");
    } finally {
      setBusy(false);
    }
  }, []);

  const cancelNow = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      setStatus(await api<UpdateStatus>("/updates/cancel", { method: "POST" }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Pembatalan gagal.");
    } finally {
      setBusy(false);
    }
  }, []);

  const installNow = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      setStatus(await api<UpdateStatus>("/updates/install", { method: "POST" }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Pemasangan update belum dapat dimulai.");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (!status || autoChecked.current) return;
    if (status.configured && status.auto_check && status.state === "idle") {
      autoChecked.current = true;
      void checkNow();
    }
  }, [status, checkNow]);

  const activeWork = Boolean(status && ["checking", "downloading", "verifying", "installing"].includes(status.state));

  useEffect(() => {
    if (!activeWork) return;
    const timer = setInterval(() => void refresh(), 1000);
    return () => clearInterval(timer);
  }, [activeWork, refresh]);

  const hasUpdate = Boolean(status && ["available", "ready_to_install", "downloading", "verifying"].includes(status.state));
  const versionLabel = `v${status?.current_version || "1.0.0"}`;

  return (
    <>
      <button
        type="button"
        className={styles.trigger}
        onClick={() => {
          setOpen(true);
          void refresh();
        }}
        aria-label="Periksa update ClipClip.ai"
      >
        {activeWork ? <LoaderCircle size={15} className="spin" /> : <RefreshCw size={15} />}
        <span className={styles.triggerLabel}>
          {status?.state === "ready_to_install"
            ? `Pasang v${status.available_version}`
            : status?.state === "available"
              ? `Update v${status.available_version}`
              : `Update · ${versionLabel}`}
        </span>
        {hasUpdate && <span className={styles.dot} aria-hidden="true" />}
      </button>

      {open && (
        <div className={styles.overlay} onClick={() => !activeWork && setOpen(false)}>
          <section
            className={styles.modal}
            role="dialog"
            aria-modal="true"
            aria-labelledby="clipclip-update-title"
            onClick={event => event.stopPropagation()}
          >
            <div className={styles.header}>
              <div className={styles.titleWrap}>
                <div className={styles.badgeIcon}>
                  <ShieldCheck size={20} />
                </div>
                <div>
                  <h2 id="clipclip-update-title">Update ClipClip.ai</h2>
                  <p className={styles.subtitle}>Verifikasi SHA-256 wajib cocok sebelum pemasangan.</p>
                </div>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="Tutup panel update"
                onClick={() => setOpen(false)}
              >
                <X size={16} />
              </button>
            </div>

            <dl className={styles.grid}>
              <div>
                <dt>Versi terpasang</dt>
                <dd>{versionLabel}</dd>
              </div>
              <div>
                <dt>Sumber feed</dt>
                <dd>
                  {!status?.configured
                    ? "Belum dikonfigurasi"
                    : status.feed_kind === "github_releases"
                      ? `GitHub Releases (${status.feed_host})`
                      : status.feed_host}
                </dd>
              </div>
              {status?.available_version && (
                <div>
                  <dt>Versi tersedia</dt>
                  <dd>v{status.available_version} · {formatBytes(status.total_bytes)}</dd>
                </div>
              )}
              {status?.expected_sha256 && (
                <div>
                  <dt>SHA-256 rilis</dt>
                  <dd className={styles.hash} title={status.expected_sha256}>
                    {status.expected_sha256.slice(0, 16)}…
                  </dd>
                </div>
              )}
            </dl>

            {status?.verified && (
              <div className={styles.verifiedBadge}>
                <CheckCheck size={14} /> SHA-256 terverifikasi cocok 100%
              </div>
            )}

            <p className={styles.statusRow} role="status">
              {activeWork && <LoaderCircle size={15} className="spin" />}
              <span>{status?.message || "Memuat status update..."}</span>
            </p>

            {status?.release_notes && <div className={styles.notes}>{status.release_notes}</div>}

            {(status?.state === "downloading" || status?.state === "verifying") && (
              <>
                <progress
                  className={styles.progress}
                  value={Math.max(0, Math.min(100, status.percent))}
                  max={100}
                  aria-label="Progress unduhan update"
                />
                <div className={styles.stats}>
                  <span>
                    {status.percent.toLocaleString("id-ID", { maximumFractionDigits: 1 })}% ·{" "}
                    {formatBytes(status.received_bytes)} / {formatBytes(status.total_bytes)}
                  </span>
                  <span>
                    {status.speed_bps !== null ? `${formatBytes(status.speed_bps)}/dtk` : ""}
                  </span>
                </div>
              </>
            )}

            {error && <p className={styles.error} role="alert">{error}</p>}

            <div className={styles.actions}>
              {status?.configured && !activeWork && (
                <button
                  type="button"
                  className={status.state === "available" || status.state === "ready_to_install" ? "button secondary" : "button primary"}
                  disabled={busy}
                  onClick={() => void checkNow()}
                >
                  <RefreshCw size={15} /> Cek Update
                </button>
              )}

              {status && ["available", "failed", "cancelled"].includes(status.state) && status.available_version && (
                <button
                  type="button"
                  className="button primary"
                  disabled={busy}
                  onClick={() => void downloadNow()}
                >
                  <Download size={15} /> Unduh Update v{status.available_version}
                </button>
              )}

              {activeWork && status?.state !== "installing" && (
                <button
                  type="button"
                  className="button secondary"
                  disabled={busy}
                  onClick={() => void cancelNow()}
                >
                  Batalkan
                </button>
              )}

              {status?.state === "ready_to_install" && (
                <button
                  type="button"
                  className="button primary"
                  disabled={busy || !status.can_install}
                  onClick={() => void installNow()}
                >
                  <ShieldCheck size={15} /> Pasang &amp; Mulai Ulang
                </button>
              )}
            </div>

            {status?.state === "ready_to_install" && !status.can_install && (
              <p className={styles.note}>
                File update sudah terverifikasi SHA-256 di cache, tetapi pemasangan otomatis hanya aktif pada aplikasi desktop ClipClip.ai yang terpasang normal (nonaktif di mode DEV / profil QA terisolasi).
              </p>
            )}

            <details className={styles.setup}>
              <summary>Pengaturan feed update privat (backend.env lokal)</summary>
              <p className={styles.note}>
                Tambahkan di <code>%LOCALAPPDATA%\ClipClip.ai\config\backend.env</code> lalu mulai ulang ClipClip.ai (token tidak pernah disimpan di source code):
              </p>
              <pre>{`UPDATE_FEED_URL=https://api.github.com/repos/khallure/ClipClip.ai/releases/latest
UPDATE_GITHUB_TOKEN=<PAT read-only Contents jika repo privat>
UPDATE_AUTO_CHECK=false`}</pre>
            </details>
          </section>
        </div>
      )}
    </>
  );
}
