import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import * as THREE from "three";

/**
 * A spiral galaxy (three.js points, additive glow) that the user can grab and drag anywhere on
 * the page. Release while moving to throw it; it glides, bounces off the page edges and tilts
 * with its velocity. Double-click (or press Home while focused) sends it back to its start.
 * Arrow keys nudge it for keyboard users.
 *
 * Positioned absolutely inside the nearest positioned ancestor (the page root), so it scrolls
 * with the page and sits behind the glass cards, which blur it as it passes under them.
 */

const ARMS = 4;
const STARS = 42000;
const BULGE = 7000;
const RADIUS = 5;

function buildGalaxy(): THREE.Points {
  const n = STARS + BULGE;
  const pos = new Float32Array(n * 3);
  const col = new Float32Array(n * 3);
  const core = new THREE.Color("#fff6ea");
  const mid = new THREE.Color("#b3c4f5");
  const outer = new THREE.Color("#2f4c9e");
  const knot = new THREE.Color("#ffd9c2");
  const c = new THREE.Color();
  const g = () => (Math.random() + Math.random() + Math.random() - 1.5) / 1.5; // ~gaussian

  for (let i = 0; i < STARS; i++) {
    const r = RADIUS * Math.pow(Math.random(), 1.5) + 0.15;
    const arm = ((i % ARMS) / ARMS) * Math.PI * 2;
    const spin = r * 1.05;
    const spread = 0.45 * Math.pow(Math.random(), 2.6) * r;
    const a = arm + spin + g() * 0.25;
    pos[i * 3] = Math.cos(a) * r + g() * spread;
    pos[i * 3 + 1] = g() * 0.12 * (1.2 - r / RADIUS) + g() * spread * 0.25;
    pos[i * 3 + 2] = Math.sin(a) * r + g() * spread;
    const k = r / RADIUS;
    c.copy(core).lerp(mid, Math.min(1, k * 2.2));
    if (k > 0.45) c.lerp(outer, (k - 0.45) / 0.55);
    if (Math.random() < 0.02) c.copy(knot); // scattered bright knots
    const b = 0.65 + Math.random() * 0.35;
    col[i * 3] = c.r * b; col[i * 3 + 1] = c.g * b; col[i * 3 + 2] = c.b * b;
  }
  for (let i = STARS; i < n; i++) {
    const r = Math.abs(g()) * 1.1;
    const th = Math.random() * Math.PI * 2;
    const ph = Math.acos(2 * Math.random() - 1);
    pos[i * 3] = r * Math.sin(ph) * Math.cos(th);
    pos[i * 3 + 1] = r * Math.cos(ph) * 0.45;
    pos[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th);
    c.copy(core).lerp(mid, r * 0.35);
    col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("color", new THREE.BufferAttribute(col, 3));

  // soft round sprite
  const sc = document.createElement("canvas");
  sc.width = sc.height = 64;
  const sx = sc.getContext("2d")!;
  const grad = sx.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.25, "rgba(255,255,255,0.6)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  sx.fillStyle = grad;
  sx.fillRect(0, 0, 64, 64);
  const tex = new THREE.CanvasTexture(sc);

  const mat = new THREE.PointsMaterial({
    size: 0.055, sizeAttenuation: true, map: tex, vertexColors: true,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  });
  return new THREE.Points(geo, mat);
}

export default function DraggableGalaxy() {
  const wrap = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [hint, setHint] = useState(true);
  const [grabbing, setGrabbing] = useState(false);
  // shared mutable state between pointer handlers and the render loop
  const st = useRef({ x: 0, y: 0, vx: 0, vy: 0, home: { x: 0, y: 0 }, size: 420, dragging: false, ox: 0, oy: 0, lastT: 0, spinBoost: 0, returning: false });

  useEffect(() => {
    const el = wrap.current!;
    const canvas = canvasRef.current!;
    const s = st.current;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const place = () => {
      const vw = window.innerWidth;
      s.size = vw < 640 ? 320 : vw < 1024 ? 400 : vw < 1600 ? 540 : 660;
      s.home = { x: vw / 2 - s.size / 2, y: 64 };
      // Desktop: horizontally over the cards (data-galaxy-anchor), vertically level with the
      // hero title + description (data-galaxy-row), so it shares their row on load.
      if (vw >= 1024) {
        const p = el.parentElement!;
        const pr = p.getBoundingClientRect();
        const anchor = p.querySelector("[data-galaxy-anchor]");
        const row = p.querySelector("[data-galaxy-row] h1")?.parentElement;
        const h1 = p.querySelector("[data-galaxy-row] h1");
        const desc = h1?.nextElementSibling;
        const cx = anchor ? anchor.getBoundingClientRect().left - pr.left + anchor.getBoundingClientRect().width / 2 : p.clientWidth * 0.75;
        let cy = 300;
        if (row && h1 && desc) {
          const top = h1.getBoundingClientRect().top, bottom = desc.getBoundingClientRect().bottom;
          cy = (top + bottom) / 2 - pr.top;
        }
        s.home = { x: cx - s.size / 2, y: cy - s.size / 2 };
      }
      el.style.width = el.style.height = `${s.size}px`;
    };
    place();
    s.x = s.home.x; s.y = s.home.y;

    const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: "low-power" });
    renderer.setClearColor(0x000000, 0);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
    camera.position.set(0, 6, 11.5);
    camera.lookAt(0, 0, 0);
    const tiltGroup = new THREE.Group();
    const galaxy = buildGalaxy();
    tiltGroup.add(galaxy);
    tiltGroup.rotation.x = 0.25;
    scene.add(tiltGroup);

    const resize = () => {
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setSize(s.size, s.size, false);
    };
    resize();

    const bounds = () => {
      const p = el.parentElement!;
      const m = s.size * 0.3; // allow it to tuck partly off the edge
      return { minX: -m, maxX: p.clientWidth - s.size + m, minY: -m, maxY: p.scrollHeight - s.size + m };
    };

    let raf = 0, prev = performance.now();
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      const dt = Math.min(0.05, (now - prev) / 1000);
      prev = now;

      if (!s.dragging) {
        if (s.returning) {
          s.x += (s.home.x - s.x) * Math.min(1, dt * 6);
          s.y += (s.home.y - s.y) * Math.min(1, dt * 6);
          if (Math.hypot(s.home.x - s.x, s.home.y - s.y) < 0.5) s.returning = false;
          s.vx = s.vy = 0;
        } else if (Math.abs(s.vx) + Math.abs(s.vy) > 0.5) {
          s.x += s.vx * dt; s.y += s.vy * dt;
          const f = Math.pow(0.04, dt); // friction
          s.vx *= f; s.vy *= f;
          const b = bounds();
          if (s.x < b.minX) { s.x = b.minX; s.vx = Math.abs(s.vx) * 0.6; }
          if (s.x > b.maxX) { s.x = b.maxX; s.vx = -Math.abs(s.vx) * 0.6; }
          if (s.y < b.minY) { s.y = b.minY; s.vy = Math.abs(s.vy) * 0.6; }
          if (s.y > b.maxY) { s.y = b.maxY; s.vy = -Math.abs(s.vy) * 0.6; }
        }
      }
      el.style.transform = `translate3d(${s.x}px, ${s.y}px, 0)`;

      // tilt toward the direction of travel, ease back when still
      const tx = 0.25 + Math.max(-0.5, Math.min(0.5, s.vy / 2500));
      const tz = Math.max(-0.5, Math.min(0.5, -s.vx / 2500));
      tiltGroup.rotation.x += (tx - tiltGroup.rotation.x) * Math.min(1, dt * 5);
      tiltGroup.rotation.z += (tz - tiltGroup.rotation.z) * Math.min(1, dt * 5);
      s.spinBoost *= Math.pow(0.3, dt);
      galaxy.rotation.y -= dt * ((reduced ? 0 : 0.07) + s.spinBoost);
      renderer.render(scene, camera);
    };
    raf = requestAnimationFrame(loop);

    const onResize = () => {
      const atHome = Math.hypot(s.x - s.home.x, s.y - s.home.y) < 2;
      place();
      resize();
      if (atHome) { s.x = s.home.x; s.y = s.home.y; }
    };
    window.addEventListener("resize", onResize);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
      galaxy.geometry.dispose();
      const m = galaxy.material as THREE.PointsMaterial;
      m.map?.dispose();
      m.dispose();
      renderer.dispose();
    };
  }, []);

  const parentPoint = (e: PointerEvent) => {
    const r = wrap.current!.parentElement!.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };

  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    const s = st.current;
    const p = parentPoint(e);
    s.dragging = true; s.returning = false;
    s.ox = p.x - s.x; s.oy = p.y - s.y;
    s.vx = s.vy = 0; s.lastT = performance.now();
    e.currentTarget.setPointerCapture(e.pointerId);
    setGrabbing(true);
    setHint(false);
  };
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    const s = st.current;
    if (!s.dragging) return;
    const p = parentPoint(e);
    const nx = p.x - s.ox, ny = p.y - s.oy;
    const now = performance.now();
    const dt = Math.max(1, now - s.lastT) / 1000;
    // smoothed velocity in px/s, used for the throw and the tilt
    s.vx = s.vx * 0.6 + ((nx - s.x) / dt) * 0.4;
    s.vy = s.vy * 0.6 + ((ny - s.y) / dt) * 0.4;
    s.spinBoost = Math.min(2.5, s.spinBoost + Math.hypot(nx - s.x, ny - s.y) * 0.002);
    s.x = nx; s.y = ny; s.lastT = now;
  };
  const onUp = () => {
    const s = st.current;
    s.dragging = false;
    if (performance.now() - s.lastT > 80) s.vx = s.vy = 0; // paused before releasing: no throw
    setGrabbing(false);
  };
  const goHome = () => { st.current.returning = true; };
  const onKey = (e: KeyboardEvent) => {
    const s = st.current;
    const step = e.shiftKey ? 80 : 20;
    const moves: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (e.key === "Home") { e.preventDefault(); goHome(); return; }
    const m = moves[e.key];
    if (!m) return;
    e.preventDefault();
    s.returning = false;
    s.x += m[0]; s.y += m[1];
    s.spinBoost = Math.min(2.5, s.spinBoost + 0.3);
    setHint(false);
  };

  return (
    <div ref={wrap} className="pointer-events-none absolute left-0 top-0 z-10 will-change-transform" style={{ width: 420, height: 420 }}>
      {/* halo behind the disc */}
      <canvas ref={canvasRef} aria-hidden className="relative h-full w-full [mask-image:radial-gradient(circle,#000_52%,transparent_70%)]" />
      {/* grab handle: only the disc itself is draggable, not the whole square */}
      <div
        role="button" tabIndex={0}
        aria-label="Galaxy: drag to move it around the page. Arrow keys nudge it; Home or double-click returns it."
        title="Drag me · double-click to return"
        className={`pointer-events-auto absolute inset-[20%] touch-none select-none rounded-full focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white/60 ${grabbing ? "cursor-grabbing" : "cursor-grab"}`}
        onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
        onDoubleClick={goHome} onKeyDown={onKey}
      />
      <div className={`pointer-events-none absolute top-[24%] left-1/2 -translate-x-1/2 whitespace-nowrap font-mono text-[11px] text-neutral-500 transition-opacity duration-700 ${hint ? "opacity-100" : "opacity-0"}`}>
        drag to move
      </div>
    </div>
  );
}
