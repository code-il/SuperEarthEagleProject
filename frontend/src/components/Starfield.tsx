import { useEffect, useRef, useState } from "react";
import * as THREE from "three";

/**
 * Full-viewport interactive star field, used as the site background.
 *
 * - Near stars live in a cube that wraps around the camera on every axis (in the vertex shader),
 *   so you can fly forever in any direction and never reach an edge.
 * - A distant Milky Way band (blue-white star clouds + deep-blue dust glow, faint warm core) sits on a sky sphere.
 * - Interaction: pointer parallax; drag empty space to look around (with inertia);
 *   scrolling flies you through the stars; a click fires a short warp.
 */

const R = 420;            // half-size of the wrapping star cube
const NEAR_STARS = 9000;
const BAND_STARS = 80000;
const SKY_RADIUS = 1500;

const STAR_FRAG = /* glsl */ `
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    float r = length(gl_PointCoord - 0.5) * 2.0;
    if (r > 1.0) discard;
    float a = pow(1.0 - r, 2.2) + smoothstep(0.3, 0.0, r) * 0.6;
    gl_FragColor = vec4(vColor, a * vAlpha);
  }
`;

const NEAR_VERT = /* glsl */ `
  uniform vec3 uCam;
  uniform float uR;
  uniform float uTime;
  uniform float uPx;
  uniform float uWarp;
  attribute float aSize;
  attribute float aPhase;
  attribute vec3 aColor;
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    vec3 rel = mod(position - uCam + uR, 2.0 * uR) - uR;   // infinite tiling around the camera
    vec4 mv = viewMatrix * vec4(rel, 1.0);
    gl_Position = projectionMatrix * mv;
    float d = max(-mv.z, 0.5);
    float tw = 0.7 + 0.3 * sin(uTime * (0.6 + aPhase * 2.5) + aPhase * 40.0);
    float s = aSize * uPx / d * (1.0 + uWarp * 0.6);
    gl_PointSize = clamp(s, 1.0, 16.0);
    float fade = smoothstep(uR, uR * 0.5, length(rel)) * smoothstep(1.0, 12.0, d);
    vAlpha = fade * tw * min(1.0, s);
    vColor = aColor;
  }
`;

const BAND_VERT = /* glsl */ `
  uniform float uTime;
  uniform float uDpr;
  attribute float aSize;
  attribute float aPhase;
  attribute vec3 aColor;
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mv;
    gl_PointSize = aSize * uDpr;
    vAlpha = 0.8 + 0.2 * sin(uTime * (0.4 + aPhase) + aPhase * 30.0);
    vColor = aColor;
  }
`;

const gauss = () => {
  let u = 0, v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
};

const STAR_TINTS = ["#ffffff", "#eef3ff", "#dbe5ff", "#c6d5ff", "#b4c6ff", "#fff1e0"].map((c) => new THREE.Color(c));

function nearStars(px: number) {
  const pos = new Float32Array(NEAR_STARS * 3);
  const col = new Float32Array(NEAR_STARS * 3);
  const size = new Float32Array(NEAR_STARS);
  const phase = new Float32Array(NEAR_STARS);
  for (let i = 0; i < NEAR_STARS; i++) {
    pos[i * 3] = (Math.random() * 2 - 1) * R;
    pos[i * 3 + 1] = (Math.random() * 2 - 1) * R;
    pos[i * 3 + 2] = (Math.random() * 2 - 1) * R;
    const c = STAR_TINTS[Math.floor(Math.random() * STAR_TINTS.length)];
    col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
    size[i] = 2 + Math.pow(Math.random(), 5) * 9; // mostly small, a few bright
    phase[i] = Math.random();
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("aColor", new THREE.BufferAttribute(col, 3));
  geo.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
  geo.setAttribute("aPhase", new THREE.BufferAttribute(phase, 1));
  const mat = new THREE.ShaderMaterial({
    vertexShader: NEAR_VERT, fragmentShader: STAR_FRAG,
    uniforms: { uCam: { value: new THREE.Vector3() }, uR: { value: R }, uTime: { value: 0 }, uPx: { value: px }, uWarp: { value: 0 } },
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  });
  const pts = new THREE.Points(geo, mat);
  pts.frustumCulled = false;
  return pts;
}

/** Distant Milky Way: a tilted great-circle band with a bright bulge, plus faint all-sky stars. */
function milkyWay(dpr: number) {
  const pos = new Float32Array(BAND_STARS * 3);
  const col = new Float32Array(BAND_STARS * 3);
  const size = new Float32Array(BAND_STARS);
  const phase = new Float32Array(BAND_STARS);
  const cool = new THREE.Color("#a9bdf5"), grey = new THREE.Color("#3e5aa6"), warm = new THREE.Color("#efdcc2"), white = new THREE.Color("#ffffff");
  const c = new THREE.Color();
  for (let i = 0; i < BAND_STARS; i++) {
    let l: number, b: number;
    const kind = Math.random();
    if (kind < 0.22) { l = (Math.random() * 2 - 1) * Math.PI; b = Math.asin(Math.random() * 2 - 1); }  // all-sky
    else if (kind < 0.42) { l = gauss() * 0.35; b = gauss() * 0.12; }                                   // bulge
    else { l = (Math.random() * 2 - 1) * Math.PI; b = gauss() * (0.05 + 0.06 * Math.random()); }       // disc
    const cb = Math.cos(b);
    pos[i * 3] = SKY_RADIUS * cb * Math.sin(l);
    pos[i * 3 + 1] = SKY_RADIUS * Math.sin(b);
    pos[i * 3 + 2] = -SKY_RADIUS * cb * Math.cos(l);
    const centre = Math.exp(-(l * l) / 0.5);
    c.copy(grey).lerp(cool, Math.random()).lerp(warm, centre * 0.45 * Math.random()).lerp(white, Math.random() * 0.4);
    const br = kind < 0.22 ? 0.35 + Math.random() * 0.55 : 0.2 + Math.random() * 0.5;
    col[i * 3] = c.r * br; col[i * 3 + 1] = c.g * br; col[i * 3 + 2] = c.b * br;
    size[i] = 1.3 + Math.pow(Math.random(), 7) * 4.5;
    phase[i] = Math.random();
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("aColor", new THREE.BufferAttribute(col, 3));
  geo.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
  geo.setAttribute("aPhase", new THREE.BufferAttribute(phase, 1));
  const mat = new THREE.ShaderMaterial({
    vertexShader: BAND_VERT, fragmentShader: STAR_FRAG,
    uniforms: { uTime: { value: 0 }, uDpr: { value: dpr } },
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  });
  const pts = new THREE.Points(geo, mat);
  pts.frustumCulled = false;

  // nebula glow along the band
  const tc = document.createElement("canvas");
  tc.width = tc.height = 128;
  const g = tc.getContext("2d")!;
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.4, "rgba(255,255,255,0.35)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  const tex = new THREE.CanvasTexture(tc);
  const hues = ["#16307a", "#1b3a8a", "#0f2560", "#24489e"]; // deep-blue dust glow along the band
  const neb = new THREE.Group();
  for (let i = 0; i < 46; i++) {
    const l = i < 12 ? gauss() * 0.4 : (Math.random() * 2 - 1) * Math.PI;
    const b = gauss() * 0.08;
    const m = new THREE.SpriteMaterial({
      map: tex, color: hues[i % hues.length], transparent: true, depthWrite: false,
      blending: THREE.AdditiveBlending, opacity: (i < 12 ? 0.2 : 0.12) * (0.6 + Math.random() * 0.6),
    });
    const s = new THREE.Sprite(m);
    const cb = Math.cos(b);
    s.position.set(SKY_RADIUS * 0.95 * cb * Math.sin(l), SKY_RADIUS * 0.95 * Math.sin(b), -SKY_RADIUS * 0.95 * cb * Math.cos(l));
    const k = (i < 12 ? 500 : 300) + Math.random() * 450;
    s.scale.set(k * (1.4 + Math.random()), k, 1);
    s.material.rotation = (Math.random() - 0.5) * 0.4;
    neb.add(s);
  }

  const sky = new THREE.Group();
  sky.add(pts, neb);
  sky.rotation.set(-0.2, -0.08, -0.45); // band crosses the screen diagonally, bulge in view
  return { sky, pts, tex, neb };
}

export default function Starfield() {
  const ref = useRef<HTMLCanvasElement>(null);
  const [hint, setHint] = useState(true);

  useEffect(() => {
    const canvas = ref.current!;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: "high-performance" });
    renderer.setClearColor(0x000000, 1);
    renderer.setPixelRatio(dpr);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(60, 1, 0.5, SKY_RADIUS * 1.2);
    camera.rotation.order = "YXZ";

    const near = nearStars(60 * dpr);
    const { sky, pts: band, tex, neb } = milkyWay(dpr);
    scene.add(sky, near);
    const nearU = (near.material as THREE.ShaderMaterial).uniforms;
    const bandU = (band.material as THREE.ShaderMaterial).uniforms;

    const resize = () => {
      const w = window.innerWidth, h = window.innerHeight;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      nearU.uPx.value = h * 0.14 * dpr;
    };
    resize();

    // view + motion state
    const v = {
      yaw: 0, pitch: 0, vYaw: 0, vPitch: 0,
      px: 0, py: 0, tpx: 0, tpy: 0,          // pointer parallax (current, target)
      speed: reduced ? 0 : 5, boost: 0,      // forward travel, units/s
      dragging: false, moved: 0, lx: 0, ly: 0, lastScroll: window.scrollY,
    };
    const cam = new THREE.Vector3();
    const fwd = new THREE.Vector3();

    const onPointerMove = (e: PointerEvent) => {
      v.tpx = (e.clientX / window.innerWidth) * 2 - 1;
      v.tpy = (e.clientY / window.innerHeight) * 2 - 1;
      if (!v.dragging) return;
      const dx = e.clientX - v.lx, dy = e.clientY - v.ly;
      v.lx = e.clientX; v.ly = e.clientY;
      v.moved += Math.abs(dx) + Math.abs(dy);
      const k = 0.0028;
      v.yaw += dx * k; v.pitch += dy * k;
      v.vYaw = dx * k * 60; v.vPitch = dy * k * 60;
    };
    const onDown = (e: PointerEvent) => {
      if (e.button !== 0) return;
      v.dragging = true; v.moved = 0; v.lx = e.clientX; v.ly = e.clientY;
      v.vYaw = v.vPitch = 0;
      canvas.setPointerCapture(e.pointerId);
      canvas.style.cursor = "grabbing";
    };
    const onUp = () => {
      if (!v.dragging) return;
      v.dragging = false;
      canvas.style.cursor = "";
      if (v.moved < 6) v.boost += 260; // a click, not a drag: warp
      setHint(false);
    };
    const onScroll = () => {
      const dy = window.scrollY - v.lastScroll;
      v.lastScroll = window.scrollY;
      v.boost += dy * 1.6; // scroll down flies forward, up flies back
    };
    canvas.addEventListener("pointerdown", onDown);
    window.addEventListener("pointermove", onPointerMove, { passive: true });
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", resize);

    let raf = 0, prev = performance.now(), t = 0;
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      const dt = Math.min(0.05, (now - prev) / 1000);
      prev = now;
      t += dt;

      if (!v.dragging) {
        const f = Math.pow(0.08, dt);
        v.vYaw *= f; v.vPitch *= f;
        v.yaw += v.vYaw * dt + (reduced ? 0 : dt * 0.006); // slow drift
        v.pitch += v.vPitch * dt;
      }
      v.pitch = Math.max(-1.2, Math.min(1.2, v.pitch));
      v.px += (v.tpx - v.px) * Math.min(1, dt * 2.5);
      v.py += (v.tpy - v.py) * Math.min(1, dt * 2.5);
      camera.rotation.y = -v.yaw - v.px * 0.06;
      camera.rotation.x = -v.pitch - v.py * 0.04;

      v.boost *= Math.pow(0.15, dt);
      const speed = v.speed + v.boost;
      camera.getWorldDirection(fwd);
      cam.addScaledVector(fwd, speed * dt);
      nearU.uCam.value.copy(cam);
      nearU.uTime.value = t;
      nearU.uWarp.value = Math.min(3, Math.abs(v.boost) / 150);
      bandU.uTime.value = t;
      renderer.render(scene, camera);
    };
    raf = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(raf);
      canvas.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", resize);
      near.geometry.dispose(); (near.material as THREE.Material).dispose();
      band.geometry.dispose(); (band.material as THREE.Material).dispose();
      neb.children.forEach((s) => ((s as THREE.Sprite).material as THREE.Material).dispose());
      tex.dispose();
      renderer.dispose();
    };
  }, []);

  return (
    <div className="fixed inset-0 z-0 bg-black">
      <canvas ref={ref} aria-label="Interactive star field: drag to look around, scroll to fly, click to warp"
        className="pointer-events-auto h-full w-full cursor-grab touch-pan-y" />
      {/* very faint dark-blue wash (screen blend, so it tints the black without dulling stars) */}
      <div aria-hidden className="pointer-events-none absolute inset-0 mix-blend-screen bg-[radial-gradient(ellipse_70%_55%_at_15%_0%,rgba(24,40,110,0.38),transparent_70%),radial-gradient(ellipse_60%_55%_at_95%_100%,rgba(16,30,90,0.32),transparent_70%),radial-gradient(ellipse_120%_90%_at_50%_50%,rgba(6,12,38,0.6),transparent_85%)]" />
      {/* vignette keeps text readable */}
      <div aria-hidden className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_45%,rgba(0,0,0,0.6)_100%)]" />
      <div className={`pointer-events-none absolute bottom-5 left-1/2 hidden -translate-x-1/2 font-mono text-[11px] text-neutral-500 transition-opacity duration-700 sm:block ${hint ? "opacity-100" : "opacity-0"}`}>
        drag the sky to look around · scroll to fly · click to warp
      </div>
    </div>
  );
}
