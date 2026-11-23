"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import {
  Plate,
  Legible,
  f30,
  frameInfo,
  layoutPage,
  legibilityFor,
  loadFonts,
  pageStyle,
  pageTimings,
  resolvePalette,
  wordStates,
  type CaptionsFile,
  type Layer,
  type Style,
} from "@/lib/subtle/engine";
import { REGISTRY } from "@/lib/subtle/styles";

const VERT_SRC = `
attribute vec2 a_pos;
varying vec2 v_uv;
void main() {
  v_uv = a_pos * 0.5 + 0.5;
  v_uv.y = 1.0 - v_uv.y;
  gl_Position = vec4(a_pos, 0.0, 1.0);
}`;

const FRAG_SRC = `
precision mediump float;
uniform sampler2D u_tex;
varying vec2 v_uv;
void main() {
  vec4 rgb = texture2D(u_tex, vec2(v_uv.x * 0.5, v_uv.y));
  float mask = texture2D(u_tex, vec2(0.5 + v_uv.x * 0.5, v_uv.y)).r;
  gl_FragColor = vec4(rgb.rgb, mask);
}`;

function compileShader(gl: WebGLRenderingContext, type: number, src: string): WebGLShader | null {
  const sh = gl.createShader(type);
  if (!sh) return null;
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    gl.deleteShader(sh);
    return null;
  }
  return sh;
}

type LayerStageProps = {
  captions: CaptionsFile;
  style: Style;
  frame: number;
  layer: Layer;
  interactive?: boolean;
  onToggleWord?: (pageIndex: number, wordIndex: number, text: string, emphasized: boolean) => void;
};

function SubtleLayerStage({ captions, style, frame, layer, interactive, onToggleWord }: LayerStageProps) {
  const { width, height, fps } = captions.video;
  const f = useMemo(() => frameInfo(width, height, fps || 30), [width, height, fps]);
  const def = REGISTRY[style.anim?.type] ?? REGISTRY[style.name];
  const timings = useMemo(
    () => (def ? pageTimings(captions.pages, fps || 30, def.minShow ? f30(def.minShow, fps || 30) : 0, !!captions.beats) : []),
    [captions, fps, def],
  );
  const wantPage = layer === "all" || layer === "front";
  const layouts = useMemo(() => {
    if (!def || !wantPage) return [];
    const o = def.layout?.(style, f) ?? {};
    return captions.pages.map((p) => layoutPage(p, pageStyle(style, p), f, { ...o, specs: def.spec?.(p, style) }));
  }, [captions, style, f, wantPage, def]);

  if (!def) return null;

  const idx = wantPage ? timings.findIndex((t) => frame >= t.s && frame < t.e) : -1;
  let pageNode: React.ReactNode = null;
  if (idx >= 0 && layouts[idx]) {
    const t = timings[idx];
    const P = def.Page;
    const lg = legibilityFor(captions, style, idx);
    pageNode = (
      <Legible lg={lg} f={f} L={layouts[idx]} since={frame - t.s} left={t.e - frame} pageIndex={idx}>
        <P
          st={pageStyle(style, captions.pages[idx])}
          f={f}
          page={captions.pages[idx]}
          pageIndex={idx}
          L={layouts[idx]}
          t={t}
          frame={frame}
          since={frame - t.s}
          left={t.e - frame}
          ws={wordStates(t, frame)}
          mode="visible"
          captions={captions}
          layer={layer}
        />
      </Legible>
    );
  }

  const BigC = def.Big;
  const bigAll = captions.big ?? [];
  const bigList =
    layer === "all"
      ? bigAll
      : layer === "behind"
        ? bigAll.filter((b) => !b.front)
        : layer === "front"
          ? bigAll.filter((b) => b.front)
          : [];
  const bigNode =
    bigList.length && BigC && style.behind?.enabled ? (
      <BigC st={style} f={f} big={bigList} frame={frame} captions={captions} layer={layer} />
    ) : null;

  const currentLayout = idx >= 0 ? layouts[idx] : null;
  const currentPage = idx >= 0 ? captions.pages[idx] : null;

  return (
    <div style={{ position: "absolute", inset: 0, width, height, pointerEvents: "none" }}>
      <Plate f={f} mask={false}>
        {bigNode}
        {pageNode}
      </Plate>
      {interactive && currentLayout && currentPage && onToggleWord && (
        <div style={{ position: "absolute", inset: 0, pointerEvents: "auto" }}>
          {currentLayout.words.map((wb) => {
            const orig = currentPage.words[wb.i];
            if (!orig) return null;
            const lineObj = currentLayout.lines[wb.line];
            const top = (lineObj ? lineObj.y : currentLayout.cy) - currentLayout.lineH * 0.55;
            const emphasized = Boolean(orig.emphasis);
            return (
              <button
                key={`${idx}-${wb.i}`}
                type="button"
                title={emphasized ? `Hapus highlight "${orig.text}"` : `Highlight "${orig.text}"`}
                onClick={(ev) => {
                  ev.stopPropagation();
                  onToggleWord(idx, wb.i, orig.text, emphasized);
                }}
                style={{
                  position: "absolute",
                  left: Math.max(0, wb.left - 6),
                  top: Math.max(0, top),
                  width: Math.max(16, wb.w + 12),
                  height: Math.max(20, currentLayout.lineH * 1.1),
                  background: emphasized ? "rgba(212, 247, 104, 0.16)" : "transparent",
                  border: emphasized ? "1.5px dashed rgba(212, 247, 104, 0.85)" : "1px dashed rgba(255, 255, 255, 0.35)",
                  borderRadius: 6,
                  cursor: "pointer",
                  padding: 0,
                }}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

export function SubtleOverlay({
  videoRef,
  captions,
  style: presetStyle,
  clipStart,
  enabled,
  behindActive = false,
  mattePreviewUrl = null,
  interactive = false,
  onToggleWord,
}: {
  videoRef: RefObject<HTMLVideoElement | null>;
  captions: CaptionsFile | null;
  style: Style | null;
  clipStart: number;
  enabled: boolean;
  behindActive?: boolean;
  mattePreviewUrl?: string | null;
  interactive?: boolean;
  onToggleWord?: (pageIndex: number, wordIndex: number, text: string, emphasized: boolean) => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const matteVideoRef = useRef<HTMLVideoElement>(null);
  const glCanvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 0, h: 0 });
  const [frame, setFrame] = useState(0);
  const [fontsReady, setFontsReady] = useState(false);
  const [matteReady, setMatteReady] = useState(false);

  const resolvedStyle = useMemo(() => {
    if (!presetStyle || !captions) return null;
    return resolvePalette(presetStyle, (captions as unknown as { palette?: Record<string, string> }).palette);
  }, [presetStyle, captions]);

  useEffect(() => {
    if (!enabled || !resolvedStyle) {
      setFontsReady(false);
      return;
    }
    let alive = true;
    setFontsReady(false);
    loadFonts(resolvedStyle)
      .then(() => {
        if (alive) setFontsReady(true);
      })
      .catch(() => {
        if (alive) setFontsReady(true);
      });
    return () => {
      alive = false;
    };
  }, [enabled, resolvedStyle]);

  const overlayActive = Boolean(enabled && captions && resolvedStyle);

  useLayoutEffect(() => {
    const el = rootRef.current;
    if (!el || !overlayActive) return;
    const measure = () => {
      const rect = el.getBoundingClientRect();
      setSize({ w: rect.width, h: rect.height });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [overlayActive, captions?.video.width, captions?.video.height]);

  const fps = captions?.video.fps || 30;
  useEffect(() => {
    if (!enabled) return;
    const video = videoRef.current;
    if (!video) return;
    let raf = 0;
    const syncFrame = () => {
      const rel = Math.max(0, video.currentTime - clipStart);
      const nextFrame = Math.max(0, Math.floor(rel * fps + 1e-4));
      setFrame((prev) => (prev === nextFrame ? prev : nextFrame));
      const mv = matteVideoRef.current;
      if (mv && behindActive && mattePreviewUrl && mv.readyState >= 2) {
        if (Math.abs(mv.currentTime - rel) > 0.045) {
          try {
            mv.currentTime = rel;
          } catch {
            // ignore seek before metadata
          }
        }
        if (video.paused && !mv.paused) mv.pause();
        else if (!video.paused && mv.paused) void mv.play().catch(() => {});
      }
      if (!video.paused && !video.ended) {
        raf = requestAnimationFrame(syncFrame);
      }
    };
    syncFrame();
    const onPlay = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(syncFrame);
    };
    video.addEventListener("play", onPlay);
    video.addEventListener("timeupdate", syncFrame);
    video.addEventListener("seeked", syncFrame);
    return () => {
      cancelAnimationFrame(raf);
      video.removeEventListener("play", onPlay);
      video.removeEventListener("timeupdate", syncFrame);
      video.removeEventListener("seeked", syncFrame);
    };
  }, [enabled, videoRef, clipStart, fps, behindActive, mattePreviewUrl]);

  // Packed WebGL cutout renderer for live Behind-the-Speaker preview
  useEffect(() => {
    setMatteReady(false);
    if (!enabled || !behindActive || !mattePreviewUrl) return;
    const canvas = glCanvasRef.current;
    const mv = matteVideoRef.current;
    if (!canvas || !mv) return;
    const gl = canvas.getContext("webgl", { alpha: true, premultipliedAlpha: false });
    if (!gl) return;

    const vs = compileShader(gl, gl.VERTEX_SHADER, VERT_SRC);
    const fs = compileShader(gl, gl.FRAGMENT_SHADER, FRAG_SRC);
    if (!vs || !fs) return;
    const prog = gl.createProgram();
    if (!prog) return;
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return;
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, "a_pos");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);

    let stopped = false;
    let raf = 0;
    const draw = () => {
      if (stopped) return;
      if (mv.readyState >= 2 && mv.videoWidth > 0 && mv.videoHeight > 0) {
        const halfW = Math.max(1, Math.floor(mv.videoWidth / 2));
        if (canvas.width !== halfW || canvas.height !== mv.videoHeight) {
          canvas.width = halfW;
          canvas.height = mv.videoHeight;
          gl.viewport(0, 0, halfW, mv.videoHeight);
        }
        gl.clearColor(0, 0, 0, 0);
        gl.clear(gl.COLOR_BUFFER_BIT);
        gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, mv);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        setMatteReady(true);
      }
      raf = requestAnimationFrame(draw);
    };
    const onMeta = () => draw();
    mv.addEventListener("loadeddata", onMeta);
    raf = requestAnimationFrame(draw);
    return () => {
      stopped = true;
      cancelAnimationFrame(raf);
      mv.removeEventListener("loadeddata", onMeta);
      gl.deleteTexture(tex);
      gl.deleteBuffer(buf);
      gl.deleteProgram(prog);
      gl.deleteShader(vs);
      gl.deleteShader(fs);
    };
  }, [enabled, behindActive, mattePreviewUrl]);

  if (!enabled || !captions || !resolvedStyle) return null;

  const scale = size.w > 0 ? size.w / Math.max(1, captions.video.width) : 0;
  const stageReady = fontsReady && size.w > 0;
  const useDual = behindActive && Boolean(mattePreviewUrl) && matteReady;

  return (
    <div
      ref={rootRef}
      style={{
        position: "absolute",
        inset: 0,
        overflow: "hidden",
        pointerEvents: "none",
        zIndex: 5,
      }}
    >
      {behindActive && mattePreviewUrl && (
        <video
          ref={matteVideoRef}
          src={mattePreviewUrl}
          muted
          playsInline
          preload="auto"
          style={{ display: "none" }}
        />
      )}

      {stageReady && useDual && (
        <div
          style={{
            width: captions.video.width,
            height: captions.video.height,
            transform: `scale(${scale})`,
            transformOrigin: "top left",
            position: "absolute",
            left: 0,
            top: 0,
            pointerEvents: "none",
          }}
        >
          <SubtleLayerStage captions={captions} style={resolvedStyle} frame={frame} layer="behind" />
        </div>
      )}

      {behindActive && mattePreviewUrl && (
        <canvas
          ref={glCanvasRef}
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            opacity: useDual ? 1 : 0,
            pointerEvents: "none",
          }}
        />
      )}

      {stageReady && (
        <div
          style={{
            width: captions.video.width,
            height: captions.video.height,
            transform: `scale(${scale})`,
            transformOrigin: "top left",
            position: "absolute",
            left: 0,
            top: 0,
            pointerEvents: "none",
          }}
        >
          <SubtleLayerStage
            captions={captions}
            style={resolvedStyle}
            frame={frame}
            layer={useDual ? "front" : "all"}
            interactive={interactive}
            onToggleWord={onToggleWord}
          />
        </div>
      )}
    </div>
  );
}
