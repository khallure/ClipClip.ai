"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { Check, ChevronDown, Crosshair, Info, LoaderCircle, LockKeyhole, Plus, RotateCcw, Save, ScanFace, Trash2 } from "lucide-react";
import { api, timecode } from "@/lib/api";
import { clamp, cropAt, poseAt, shotAt } from "@/lib/reframe";
import type { Health, Job, ReframeKeyframe, ReframePlan, ReframeShot, SpeakerOverride } from "@/lib/types";

export type ReframeState = { plan: ReframePlan | null; dirty: boolean; working: boolean };

function restoreKeys(shot: ReframeShot): ReframeKeyframe[] {
  const keys=shot.auto_keyframes?.length?shot.auto_keyframes:shot.keyframes.filter(key=>!key.manual);
  return keys.length?keys.map(key=>({...key,manual:false})):[{time:shot.start,x:.5,y:.5,zoom:1,manual:false}];
}

function drawFrame(canvas: HTMLCanvasElement, player: HTMLVideoElement, plan: ReframePlan, overview: boolean) {
  if (player.readyState < 2 || !player.videoWidth) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const time = player.currentTime, crop = cropAt(plan, time);
  ctx.fillStyle = "#050805"; ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (overview) {
    ctx.drawImage(player, 0, 0, canvas.width, canvas.height);
    const shot = shotAt(plan, time);
    const sample = shot.face_samples.reduce<typeof shot.face_samples[number] | null>((best, item) => !best || Math.abs(item.time - time) < Math.abs(best.time - time) ? item : best, null);
    if (sample && Math.abs(sample.time - time) < 1.2) {
      ctx.lineWidth = 2; ctx.font = "12px sans-serif";
      for (const face of sample.faces) {
        const [x, y, width, height] = face.bbox;
        ctx.strokeStyle = "#74dce0"; ctx.strokeRect(x * canvas.width, y * canvas.height, width * canvas.width, height * canvas.height);
        ctx.fillStyle = "#74dce0"; ctx.fillText(`Wajah ${face.track_id} · ${Math.round(face.confidence * 100)}%`, x * canvas.width, Math.max(13, y * canvas.height - 5));
      }
    }
    if (crop.focus !== "both") {
      const x = crop.x / plan.source_width * canvas.width, y = crop.y / plan.source_height * canvas.height;
      const w = crop.width / plan.source_width * canvas.width, h = crop.height / plan.source_height * canvas.height;
      ctx.fillStyle = "rgba(0,0,0,.46)";
      ctx.fillRect(0, 0, x, canvas.height); ctx.fillRect(x + w, 0, canvas.width - x - w, canvas.height);
      ctx.fillRect(x, 0, w, y); ctx.fillRect(x, y + h, w, canvas.height - y - h);
      ctx.strokeStyle = "#d7f35c"; ctx.lineWidth = 2; ctx.strokeRect(x + 1, y + 1, Math.max(0, w - 2), Math.max(0, h - 2));
    }
  } else {
    // Match FFmpeg's even-pixel scale and pad rather than stretching the crop.
    const factor = Math.min(1080 / crop.width, 1920 / crop.height);
    const w = Math.min(1080,Math.max(2,Math.floor(crop.width*factor/2)*2))*canvas.width/1080;
    const h = Math.min(1920,Math.max(2,Math.floor(crop.height*factor/2)*2))*canvas.height/1920;
    ctx.drawImage(player,crop.x,crop.y,crop.width,crop.height,(canvas.width-w)/2,(canvas.height-h)/2,w,h);
  }
}

function FrameCanvas({ videoRef, plan, overview = false, onClick }: { videoRef: RefObject<HTMLVideoElement | null>; plan: ReframePlan; overview?: boolean; onClick?: () => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    let frame = 0, previousTime = -1;
    const tick = () => {
      const player = videoRef.current, canvas = canvasRef.current;
      if (player && canvas && player.currentTime !== previousTime && player.readyState >= 2) {
        drawFrame(canvas, player, plan, overview); previousTime = player.currentTime;
      }
      frame = requestAnimationFrame(tick);
    };
    tick(); return () => cancelAnimationFrame(frame);
  }, [videoRef, plan, overview]);
  return <canvas ref={canvasRef} width={overview ? 640 : 540} height={overview ? Math.max(2, Math.round(640 * plan.source_height / plan.source_width)) : 960}
    className={overview ? "reframe-source-map" : "reframe-canvas"} onClick={onClick}
    aria-label={overview ? "Deteksi wajah dan area crop 9:16 pada sumber" : "Pratinjau framing vertikal berdasarkan keyframe"} />;
}

export function ReframePreview({ videoRef, plan, onClick }: { videoRef: RefObject<HTMLVideoElement | null>; plan: ReframePlan; onClick?: () => void }) {
  return <FrameCanvas videoRef={videoRef} plan={plan} onClick={onClick} />;
}

export function ReframeEditor({ job, start, end, playhead, busy, videoRef, onSeek, onJob, onState }:
  { job: Job; start: number; end: number; playhead: number; busy: boolean; videoRef: RefObject<HTMLVideoElement | null>; onSeek: (time: number) => void; onJob: (job: Job) => void; onState: (state: ReframeState) => void }) {
  const [plan, setPlan] = useState<ReframePlan | null>(null);
  const [dirty, setDirty] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [mode, setMode] = useState<ReframePlan["mode"]>("manual");
  const [speakerAvailable,setSpeakerAvailable]=useState(false);
  const [speakerReason,setSpeakerReason]=useState("Memeriksa model lokal…");
  const [overrideStart,setOverrideStart]=useState(0),[overrideEnd,setOverrideEnd]=useState(1);
  const [overrideFocus,setOverrideFocus]=useState<SpeakerOverride["focus"]>("left");
  const [shotId, setShotId] = useState<string | null>(null);
  const rangeValid = Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end > start && end - start <= 600;
  const selected = plan?.shots.find(shot => shot.id === shotId) || (plan ? shotAt(plan, playhead) : null);
  const editTime = selected ? clamp(playhead, selected.start, Math.max(selected.start, selected.end - .001)) : start;
  const pose = plan ? poseAt(plan, editTime) : { x: .5, y: .5, zoom: 1 };
  const disabled = busy || working;
  const speakerInterval=selected?.speaker_intervals?.find(i=>i.start<=editTime&&editTime<i.end);
  const speakerOverride=selected?.speaker_overrides?.find(i=>i.start<=editTime&&editTime<i.end);

  useEffect(()=>{let valid=true;void api<Health>("/health").then(health=>{if(valid){setSpeakerAvailable(!!health.reframe?.active_speaker);setSpeakerReason(health.reframe?.active_speaker?"Model audio-visual lokal siap.":"Model opsional belum siap. Jalankan Setup-ActiveSpeaker lalu mulai ulang backend.");}}).catch(()=>{if(valid)setSpeakerReason("Status model lokal belum dapat dibaca.");});return()=>{valid=false;};},[]);
  useEffect(()=>{setOverrideStart(0);setOverrideEnd(selected?Math.round((selected.end-selected.start)*1000)/1000:1);},[selected?.id]);

  useEffect(() => { onState({ plan, dirty, working }); }, [plan, dirty, working, onState]);
  useEffect(() => () => onState({ plan: null, dirty: false, working: false }), [onState]);
  useEffect(() => {
    if (!rangeValid) { setPlan(null); setDirty(false); return; }
    let valid = true;
    setWorking(true); setError(null); setNotice(null); setPlan(null); setDirty(false);
    void fetch(`/api/jobs/${job.id}/reframe?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`, { cache: "no-store" })
      .then(async response => {
        if (response.status === 404) return null;
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Framing belum dapat dimuat.");
        return data as ReframePlan;
      }).then(next => { if (valid) { setPlan(next); setMode(next?.mode || "manual"); setShotId(next?.shots[0]?.id || null); } })
      .catch(err => { if (valid) setError(err instanceof Error ? err.message : "Framing belum dapat dimuat."); })
      .finally(() => { if (valid) setWorking(false); });
    return () => { valid = false; };
  }, [job.id, start, end, rangeValid, job.reframe?.id, job.reframe?.status]);
  useEffect(() => { if (plan) setShotId(shotAt(plan, playhead).id); }, [plan?.id, playhead]);

  const mutateShot = useCallback((change: (shot: ReframeShot, current: ReframePlan) => ReframeShot) => {
    setPlan(current => current ? { ...current, shots: current.shots.map(shot => shot.id === shotId ? change(shot, current) : shot) } : current);
    setDirty(true); setNotice(null);
  }, [shotId]);

  async function createManual() {
    setWorking(true); setError(null);
    try {
      const next = await api<ReframePlan>(`/jobs/${job.id}/reframe/manual`, { method: "POST", body: JSON.stringify({ start, end }) });
      setPlan(next); setMode("manual"); setShotId(next.shots[0]?.id || null); setDirty(false);
      setNotice("Framing manual tersimpan. Ubah posisi atau tambahkan keyframe pada timeline.");
    } catch (err) { setError(err instanceof Error ? err.message : "Framing manual belum berhasil dibuat."); }
    finally { setWorking(false); }
  }

  async function analyzeFaces(active=false) {
    setWorking(true); setError(null); setNotice(null);
    try {
      const next = await api<Job>(`/jobs/${job.id}/reframe/analyze`, { method: "POST", body: JSON.stringify({ start, end, mode: active?"active_speaker":"auto_face" }) });
      setMode(active?"active_speaker":"auto_face"); onJob(next);
    } catch (err) { setError(err instanceof Error ? err.message : "Analisis wajah belum dapat dimulai."); }
    finally { setWorking(false); }
  }

  async function save() {
    if (!plan) return;
    setWorking(true); setError(null);
    try {
      const next = await api<ReframePlan>(`/jobs/${job.id}/reframe`, { method: "PUT", body: JSON.stringify(plan) });
      setPlan(next); setDirty(false); setNotice("Keyframe tersimpan. Ekspor akan memakai framing ini tanpa transkripsi atau analisis klip ulang.");
    } catch (err) { setError(err instanceof Error ? err.message : "Keyframe belum dapat disimpan."); }
    finally { setWorking(false); }
  }

  function upsert(values: Partial<Pick<ReframeKeyframe, "x" | "y" | "zoom">>) {
    if (!plan || !selected) return;
    const player=videoRef.current;
    player?.pause();
    const time = Math.round(clamp(player?.currentTime ?? editTime,selected.start,Math.max(selected.start,selected.end-.001))*1000)/1000;
    const key = { ...poseAt(plan, time), ...values, time, manual: true };
    onSeek(time);
    mutateShot(shot => ({ ...shot, focus:"auto", keyframes: [...shot.keyframes.filter(item => Math.abs(item.time - time) > .0005),
      { time, x: key.x, y: key.y, zoom: key.zoom, manual: true }].sort((a, b) => a.time - b.time) }));
  }

  function focus(value: ReframeShot["focus"]) {
    if (!plan || !selected) return;
    const current = poseAt(plan, editTime);
    mutateShot(shot => ({ ...shot, focus: value,
      keyframes: value === "locked" ? [...shot.keyframes.filter(key => !key.manual && Math.abs(key.time - editTime) > .0005), { time: Math.round(editTime * 1000) / 1000, x: current.x, y: current.y, zoom: current.zoom, manual: true }].sort((a, b) => a.time - b.time)
        : value === "left" || value === "right" || value === "auto" || value === "active" ? restoreKeys(shot) : shot.keyframes }));
  }

  function addOverride() {
    if(!selected)return;
    const first=selected.start+overrideStart,last=Math.min(selected.end,selected.start+overrideEnd);
    if(!Number.isFinite(first)||!Number.isFinite(last)||first<selected.start||last<=first||selected.start+overrideEnd>selected.end+.001) {setError("Pilih interval di dalam shot dengan akhir setelah awal.");return;}
    if(selected.speaker_overrides?.some(i=>i.start<last&&i.end>first)) {setError("Interval manual bertumpang tindih. Hapus interval lama atau ubah rentangnya.");return;}
    mutateShot(shot=>({...shot,speaker_overrides:[...(shot.speaker_overrides||[]),{start:first,end:last,focus:overrideFocus}].sort((a,b)=>a.start-b.start)}));setError(null);
  }

  function changeMode(value: ReframePlan["mode"]) {
    setMode(value);
    if(value==="active_speaker")return;
    if (!plan) return;
    setPlan(current => current ? { ...current, mode: value, shots: value === "manual" ? current.shots.map(shot => {
      if (shot.keyframes.some(key=>key.manual)) return shot;
      const first = poseAt(current, shot.start);
      return { ...shot, focus: shot.focus === "both" ? "both" : "auto", keyframes: [...shot.keyframes.filter(key => !key.manual && Math.abs(key.time - shot.start) > .0005), { time: shot.start, x: first.x, y: first.y, zoom: first.zoom, manual: true }].sort((a, b) => a.time - b.time) };
    }) : current.shots } : current);
    setDirty(true); setNotice(null);
  }

  return <section className="reframe-editor" aria-label="Smart vertical reframing">
    <div className="section-heading"><span><ScanFace size={16} />Smart Reframe · 9:16</span><span className="micro">LOKAL · PER SHOT</span></div>
    <label className="reframe-mode">Mode framing<span className="select-wrap"><select aria-label="Mode reframing" value={mode} onChange={event => changeMode(event.target.value as ReframePlan["mode"])} disabled={disabled}>
      <option value="manual">Manual Reframe</option><option value="auto_face">Auto Face Tracking</option>
      <option value="active_speaker">Auto Active Speaker (Experimental)</option>
    </select><ChevronDown size={14} /></span></label>
    <p className="reframe-help">Deteksi berjalan lokal pada klip terpilih. Active Speaker memakai Light-ASD dan bukti sinkronisasi SyncNet; bagian meragukan ditandai review. {mode==="active_speaker"&&speakerReason}</p>
    <div className="reframe-actions">
      {!plan && mode === "manual" && <button className="button secondary small" disabled={disabled || !rangeValid} onClick={() => void createManual()}>{working ? <LoaderCircle size={14} className="spin" /> : <Plus size={14} />}Buat framing manual</button>}
      {mode === "auto_face" && <button className="button secondary small" disabled={disabled || dirty || !rangeValid} onClick={() => void analyzeFaces()}>{job.status === "reframing" ? <LoaderCircle size={14} className="spin" /> : <ScanFace size={14} />}{plan ? "Analisis shot dan wajah lagi" : "Analisis shot dan wajah"}</button>}
      {mode === "active_speaker" && <button className="button secondary small" disabled={disabled||dirty||!rangeValid||!speakerAvailable} onClick={()=>void analyzeFaces(true)}><ScanFace size={14}/>{plan?.analysis.active_speaker?"Muat ulang analisis tersimpan":"Analisis pembicara pada klip"}</button>}
      {plan && <button className="button secondary small" disabled={disabled || !dirty} onClick={() => void save()}><Save size={14} />Simpan framing</button>}
    </div>
    {job.reframe?.status==="processing"&&<p className="reframe-help" role="status">{job.reframe.message}</p>}
    {error && <p className="field-error" role="alert">{error}</p>}
    {notice && <p className="reframe-notice" role="status"><Check size={13} />{notice}</p>}
    {dirty && <p className="reframe-unsaved" role="status">Perubahan belum disimpan. Simpan sebelum ekspor, mengganti rentang atau berpindah proyek.</p>}
    {!plan && !working && <p className="reframe-help">Belum ada keyframe untuk rentang ini. Preview dan ekspor tetap dapat memakai crop manual V1.</p>}
    {plan && selected && <>
      <div className="reframe-overview"><FrameCanvas videoRef={videoRef} plan={plan} overview /><p><span className="reframe-crop-swatch" />Area crop 9:16 <span className="reframe-face-swatch" />Wajah terdeteksi · angka menunjukkan deteksi wajah, bukan speaker confidence</p></div>
      <div className="reframe-shot-list" aria-label="Shot dalam klip">{plan.shots.map((shot, index) => <button key={shot.id} disabled={disabled} aria-pressed={shot.id === selected.id} className={shot.id === selected.id ? "selected" : ""} onClick={() => { setShotId(shot.id); onSeek(shot.start); }}>
        <strong>Shot {index + 1}{shot.review_required && <span title="Perlu review manual">!</span>}</strong><small>{timecode(shot.start, true)}–{timecode(shot.end, true)}</small>
      </button>)}</div>
      <div className="reframe-shot-state"><strong>{selected.review_required ? "Perlu review" : selected.decision === "manual" ? "Framing manual" : selected.decision === "single_face" ? "Satu wajah terdeteksi" : selected.decision==="active_speaker"?"Audio-visual lokal":"Wajah belum terdeteksi"}</strong><span>{speakerOverride?"Pilihan manual pada interval ini":selected.keyframes.some(k=>k.manual)||!["active","auto"].includes(selected.focus)?"Framing manual diprioritaskan":speakerInterval?.source==="model"?`Wajah ${speakerInterval.track_id} · skor ASD ${speakerInterval.confidence?.toFixed(3)} (belum terkalibrasi)`:speakerInterval?"Pembicara belum pasti · framing stabil / Focus Both":"Pembicara aktif: belum dianalisis"}</span>{selected.reason && <p>{selected.reason}</p>}</div>
      <div className="reframe-focus" aria-label="Fokus shot">
        {([ ["left", "Focus Left"], ["right", "Focus Right"], ["both", "Focus Both"], ["locked", "Lock Framing"] ] as const).map(([value, label]) => <button key={value} className={selected.focus === value ? "selected" : ""} aria-pressed={selected.focus === value} disabled={disabled || (value === "locked" && selected.focus === "both")} title={value === "locked" && selected.focus === "both" ? "Focus Both sudah mempertahankan seluruh gambar secara stabil." : undefined} onClick={() => focus(value)}>{value === "locked" && <LockKeyhole size={12} />}{label}</button>)}
        <button disabled={disabled||!selected.speaker_intervals?.length} aria-pressed={selected.focus==="active"} className={selected.focus==="active"?"selected":""} onClick={()=>focus("active")}>Follow Active Speaker</button>
      </div>
      {!!selected.speaker_intervals?.length&&<fieldset className="reframe-overrides" disabled={disabled}><legend>Koreksi pembicara pada interval</legend><p className="reframe-help">Waktu relatif terhadap awal shot. Koreksi ini diprioritaskan di atas deteksi dan keyframe, tanpa analisis ulang.</p>
        <div className="reframe-actions"><label>Mulai (detik)<input aria-label="Awal interval speaker" type="number" min="0" max={selected.end-selected.start} step=".01" value={overrideStart} onChange={e=>setOverrideStart(Number(e.target.value))}/></label><label>Akhir (detik)<input aria-label="Akhir interval speaker" type="number" min="0" max={selected.end-selected.start} step=".01" value={overrideEnd} onChange={e=>setOverrideEnd(Number(e.target.value))}/></label><label>Fokus<select aria-label="Fokus interval speaker" value={overrideFocus} onChange={e=>setOverrideFocus(e.target.value as SpeakerOverride["focus"])}><option value="left">Kiri</option><option value="right">Kanan</option><option value="both">Keduanya</option><option value="locked">Kunci framing</option></select></label><button className="button secondary small" onClick={addOverride}><Plus size={13}/>Tambahkan koreksi</button></div>
        {(selected.speaker_overrides||[]).map((item,index)=><div className="reframe-actions" key={`${item.start}-${item.end}`}><button className="text-button" onClick={()=>onSeek(item.start)}>{(item.start-selected.start).toFixed(2)}–{(item.end-selected.start).toFixed(2)} dtk · {item.focus}</button><button className="icon-button" aria-label={`Hapus koreksi speaker ${index+1}`} onClick={()=>mutateShot(shot=>({...shot,speaker_overrides:shot.speaker_overrides?.filter((_,i)=>i!==index)}))}><Trash2 size={13}/></button></div>)}
      </fieldset>}
      {selected.focus === "both" && <p className="reframe-help">Focus Both mempertahankan seluruh gambar dengan ruang kosong pada kanvas 9:16. Posisi dan zoom tidak diterapkan pada mode ini.</p>}
      <div className="reframe-sliders">
        <label>X Position <output>{Math.round(pose.x * 100)}%</output><input aria-label="Reframe X Position" type="range" min="0" max="1" step=".005" value={pose.x} disabled={disabled || selected.focus === "both"} onChange={event => upsert({ x: Number(event.target.value) })} /></label>
        <label>Y Position <output>{Math.round(pose.y * 100)}%</output><input aria-label="Reframe Y Position" type="range" min="0" max="1" step=".005" value={pose.y} disabled={disabled || selected.focus === "both"} onChange={event => upsert({ y: Number(event.target.value) })} /></label>
        <label>Zoom <output>{pose.zoom.toFixed(2)}×</output><input aria-label="Reframe Zoom" type="range" min="1" max="3" step=".01" value={pose.zoom} disabled={disabled || selected.focus === "both"} onChange={event => upsert({ zoom: Number(event.target.value) })} /></label>
        <label>Smoothing <output>{Math.round(plan.smoothing * 100)}%</output><input aria-label="Reframe Smoothing" type="range" min="0" max="1" step=".05" value={plan.smoothing} disabled={disabled} onChange={event => { setPlan({ ...plan, smoothing: Number(event.target.value) }); setDirty(true); setNotice(null); }} /></label>
      </div>
      <p className="reframe-help">Posisi mengikuti pusat bingkai dan dibatasi tepi sumber. Pada zoom 1×, gerak Y dapat terbatas. Smoothing bekerja di dalam shot; pergantian kamera selalu cut langsung.</p>
      <div className="reframe-timeline" aria-label="Timeline keyframe shot">
        <div className="reframe-timeline-track"><span className="reframe-playhead" style={{ left: `${clamp((editTime - selected.start) / (selected.end - selected.start), 0, 1) * 100}%` }} />
          {selected.keyframes.map((key, index) => <button key={`${key.time}-${index}`} disabled={disabled} aria-label={`Ke keyframe ${timecode(key.time, true)} ${key.manual ? "manual" : "otomatis"}`} className={key.manual ? "manual" : "auto"} style={{ left: `${clamp((key.time - selected.start) / (selected.end - selected.start), 0, 1) * 100}%` }} onClick={() => onSeek(key.time)} />)}
        </div><div className="reframe-timeline-label"><span>{timecode(selected.start, true)}</span><strong>{timecode(editTime, true)}</strong><span>{timecode(selected.end, true)}</span></div>
      </div>
      <div className="reframe-actions"><button className="button secondary small" disabled={disabled || selected.focus === "both"} onClick={() => upsert({})}><Plus size={13} />Keyframe di playhead</button><button className="text-button" disabled={disabled} onClick={() => focus(plan.mode === "active_speaker" && selected.speaker_intervals?.length ? "active" : "auto")}><RotateCcw size={13} />Pulihkan otomatis shot ini</button></div>
      <div className="reframe-keyframes" aria-label="Daftar keyframe manual">{selected.keyframes.filter(key => key.manual).map(key => <div key={key.time}>
        <button className="text-button" disabled={disabled} onClick={() => onSeek(key.time)}><Crosshair size={12} />{timecode(key.time, true)}</button><span>X {Math.round(key.x * 100)}% · Y {Math.round(key.y * 100)}% · {key.zoom.toFixed(2)}×</span>
        <button className="icon-button" aria-label={`Hapus keyframe ${timecode(key.time, true)}`} disabled={disabled} onClick={() => mutateShot(shot => {const remaining=shot.keyframes.filter(item => !(item.manual && item.time === key.time));return {...shot,keyframes:remaining.length?remaining:restoreKeys(shot)};})}><Trash2 size={13} /></button>
      </div>)}</div>
      {selected.keyframes.some(key => key.manual) && <p className="reframe-help">Keyframe manual memiliki prioritas pada seluruh shot ini. Focus Left/Right atau Pulihkan otomatis mengembalikan jalur deteksi shot.</p>}
      {!!plan.warnings.length && <details className="reframe-warnings"><summary><Info size={12} />Catatan reframing ({plan.warnings.length})</summary>{plan.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</details>}
    </>}
  </section>;
}
