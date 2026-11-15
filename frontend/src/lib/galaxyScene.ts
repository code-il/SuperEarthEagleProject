/**
 * The 3D map engine (three.js). One continuous camera from a planetary system (AU) out to the
 * whole Milky Way (tens of kpc):
 *
 * - Floating origin: everything is drawn relative to `origin` (the focused object), so there is no
 *   float32 jitter when zoomed into a system 100 pc away.
 * - Stars are one Points cloud with a shader that sizes each star by its apparent magnitude *from the
 *   camera* (auto-exposure follows the zoom) and applies the class / source / flag / distance filters.
 * - Data coordinates are heliocentric Galactic (x → Galactic centre, z → north pole); the world is Y-up,
 *   so the Galactic plane is the horizontal "ground" (`g2w`).
 */
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { DAYS_PER_SECOND, type OrbitModel, circlePoints, ellipsePoints, habitableZone, hasPosition, jdNow, meanAnomaly, orbitFor, phaseSigma, positionAtM, toSky } from "./orbits";
import { type NbodyTrack, covers, trackEnd, trackPosition } from "./nbodyTrack";
import { AU_PER_PC, FLAG, type ClassDef, type NamedObject, type PlanetSystem, type StarBuffers, fmtDist } from "./galaxyData";

export interface Filters {
  classMask: boolean[];          // by class id
  sourceMask: boolean[];         // none, model, catalogue, spherex
  requireFlags: number;          // any of these bits (0 = no requirement)
  maxDist: number;               // pc from the Sun
  minConf: number;               // 0..1, applies to model predictions
  colorMode: 0 | 1 | 2;          // class, true colour, classification source
  uncharted: boolean;            // highlight what is unknown, dim the rest
}

export interface PickResult { i: number; screen: [number, number] }

const SRC_COLORS = ["#ff4fd8", "#f5b942", "#4ade80", "#38bdf8"];
const RINGS = [1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2000, 4000, 8000, 16000];
const GC_DIST = 8178; // pc (GRAVITY 2019)
const R_SUN_PC = 2.2547e-8;     // solar radius in pc

const g2w = (x: number, y: number, z: number) => new THREE.Vector3(x, z, -y);

const VERT = /* glsl */ `
  attribute vec4 aAttr;
  attribute float aConf;
  uniform vec3 uOrigin;
  uniform float uClassMask[11];
  uniform float uSourceMask[4];
  uniform vec3 uClassColor[11];
  uniform vec3 uSourceColor[4];
  uniform int uReqFlags;
  uniform float uMaxDist;
  uniform float uMinConf;
  uniform int uColorMode;
  uniform float uUncharted;
  uniform float uPixelRatio;
  uniform float uExposure;
  uniform float uHide;
  uniform float uSelected;
  uniform float uTime;
  uniform float uMinAlpha;
  uniform float uCamDist;
  varying vec3 vColor;
  varying float vAlpha;
  varying float vRing;

  vec3 bpRpColor(float c) {
    // Gaia BP-RP -> approximate stellar colour (hot blue ... cool red).
    float t = clamp((c + 0.4) / 4.4, 0.0, 1.0);
    vec3 a = vec3(0.62, 0.72, 1.0), b = vec3(1.0, 0.97, 0.92), d = vec3(1.0, 0.72, 0.42), e = vec3(0.95, 0.35, 0.25);
    return t < 0.25 ? mix(a, b, t / 0.25) : t < 0.55 ? mix(b, d, (t - 0.25) / 0.3) : mix(d, e, (t - 0.55) / 0.45);
  }

  void main() {
    int code = int(aAttr.x + 0.5);
    int cls = code & 15;
    int src = code >> 4;
    int fl = int(aAttr.y + 0.5);
    float rSun = length(position);
    float conf = aConf / 255.0;
    bool unknown = src == 0 || (src == 1 && (fl & 128) != 0);
    bool hide = uClassMask[cls] < 0.5 || uSourceMask[src] < 0.5 || rSun > uMaxDist
      || (uReqFlags != 0 && (fl & uReqFlags) == 0) || (src == 1 && conf < uMinConf)
      || float(gl_VertexID) == uHide;
    if (hide) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); gl_PointSize = 0.0; return; }

    vec3 g = position - uOrigin;
    vec4 mv = modelViewMatrix * vec4(g.x, g.z, -g.y, 1.0);
    gl_Position = projectionMatrix * mv;
    float d = max(length(mv.xyz), 1e-7);
    float M = aAttr.z / 8.0 - 8.0;
    float m = M + 5.0 * log(d / 10.0) / log(10.0);     // apparent magnitude seen from the camera
    float flux = pow(10.0, -0.4 * (m - uExposure));
    float size = clamp(2.2 * pow(flux, 0.22), 1.1, 26.0);
    float alpha = clamp(uMinAlpha + 0.75 * pow(flux, 0.35), uMinAlpha, 1.0);
    // The complete 100 pc sample is ~40x denser than the far sample: thin it visually when zoomed far out.
    if (rSun < 105.0) { float k = clamp(120.0 / uCamDist, 0.05, 1.0); alpha *= k; size = mix(min(size, 1.6), size, k); }

    vec3 col = uColorMode == 0 ? uClassColor[cls] : uColorMode == 1 ? (aAttr.w < 0.5 ? vec3(0.6) : bpRpColor(aAttr.w / 40.0 - 1.0)) : uSourceColor[src];
    vRing = 0.0;
    if (uUncharted > 0.5) {
      if (unknown) {
        col = uSourceColor[0];
        float pulse = 0.75 + 0.25 * sin(uTime * 3.0 + float(gl_VertexID) * 0.37);
        size = max(size * 1.4, 3.0) * pulse;
        alpha = max(alpha, 0.85);
      } else { alpha *= 0.12; size = max(1.0, size * 0.7); }
    }
    if ((fl & 1) != 0 && size > 3.0) vRing = 1.0;  // planet host: draw a ring
    if (float(gl_VertexID) == uSelected) { size = max(size, 10.0) + 6.0; vRing = 2.0; alpha = 1.0; }
    vColor = col;
    vAlpha = alpha;
    gl_PointSize = size * uPixelRatio;
  }
`;

const FRAG = /* glsl */ `
  varying vec3 vColor;
  varying float vAlpha;
  varying float vRing;
  void main() {
    vec2 p = gl_PointCoord * 2.0 - 1.0;
    float r = length(p);
    if (r > 1.0) discard;
    float core = exp(-r * r * 7.0);
    float halo = exp(-r * 3.2) * 0.35;
    float a = (core + halo) * vAlpha;
    vec3 c = vColor * (0.6 + 0.8 * core) + vec3(core * 0.35);
    if (vRing > 0.5) {
      float ring = smoothstep(0.08, 0.0, abs(r - 0.82));
      c = mix(c, vRing > 1.5 ? vec3(1.0, 0.8, 0.25) : vec3(0.3, 0.9, 1.0), ring);
      a = max(a, ring * 0.9);
    }
    gl_FragColor = vec4(c * a, a);
  }
`;

interface Anim { t0: number; dur: number; fromT: THREE.Vector3; fromD: number; toD: number; dir: THREE.Vector3; toDir: THREE.Vector3; hop: number }

export class GalaxyScene {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera: THREE.PerspectiveCamera;
  readonly controls: OrbitControls;
  /** Galactic coordinates (pc, double precision) of the world origin. */
  origin = new THREE.Vector3();
  private stars: THREE.Points;
  private mat: THREE.ShaderMaterial;
  private ringGroup = new THREE.Group();
  private disk: THREE.Mesh;
  private sun: THREE.Sprite;
  private sunBody: THREE.Mesh;
  private sunLabel: HTMLDivElement;
  private system: THREE.Group | null = null;
  private systemPlanets: { mesh: THREE.Mesh; orbit: OrbitModel; arc: THREE.Line; label: HTMLDivElement; name: string }[] = [];
  /** System-view basis: x = line of nodes (horizontal), up = on the sky, y = towards the Sun (observer). */
  private sysBasis = { x: new THREE.Vector3(1, 0, 0), up: new THREE.Vector3(0, 1, 0), y: new THREE.Vector3(0, 0, 1) };
  /** Planets whose orbit is drawn but whose position the archive does not fix. */
  positionUnknown: string[] = [];
  /** Whether the habitable zone could be computed from measured stellar parameters. */
  hzDrawn = false;
  /** Whether the star's radius is measured (and the star is drawn to scale). */
  starRadiusKnown = false;
  /** Wall-clock start (ms) and Julian date of the orbit clock: simulated date = jd0 + elapsed s × DAYS_PER_SECOND. */
  private simStart = { ms: 0, jd: 0 };
  /** Planets listed by the archive but without a or P (not drawn). */
  unplottedPlanets: string[] = [];
  private labelLayer: HTMLDivElement;
  private labelPool: HTMLDivElement[] = [];
  private ringLabels: HTMLDivElement[] = [];
  private anim: Anim | null = null;
  private raf = 0;
  private lastLabel = 0;
  private clock = new THREE.Clock();
  filters: Filters;
  selected = -1;
  /** Skip rendering (e.g. while the Explore pop-up covers the map). */
  paused = false;
  exposureBase = 9.5;
  /** Limiting apparent magnitude that still renders bright: rises slower than distance so kpc views do not saturate. */
  exposureFor(camDist: number) {
    const L = Math.log10(Math.max(camDist, 1e-6) / 10);
    return this.exposureBase + (L < 1 ? 4.2 * L : 4.2 - 2.2 * (L - 1));
  }
  /** On-screen size (CSS px) and opacity the star shader gives a star of absolute magnitude M seen from d pc. */
  starLook(M: number, d: number, nearSun: boolean): { px: number; alpha: number } {
    const u = this.mat.uniforms;
    const m = M + 5 * Math.log10(Math.max(d, 1e-7) / 10);
    const flux = Math.pow(10, -0.4 * (m - u.uExposure.value));
    let px = THREE.MathUtils.clamp(2.2 * Math.pow(flux, 0.22), 1.1, 26);
    let alpha = THREE.MathUtils.clamp(u.uMinAlpha.value + 0.75 * Math.pow(flux, 0.35), u.uMinAlpha.value, 1);
    if (nearSun) { const k = THREE.MathUtils.clamp(120 / u.uCamDist.value, 0.05, 1); alpha *= k; px = THREE.MathUtils.lerp(Math.min(px, 1.6), px, k); }
    return { px, alpha };
  }

  onFrame?: (info: { camDist: number; focusDistFromSun: number; orbitJd: number | null; engine: "kepler" | "nbody" }) => void;
  onLabelClick?: (i: number) => void;

  constructor(private host: HTMLElement, private data: StarBuffers, private classes: ClassDef[], private named: NamedObject[], filters: Filters) {
    this.filters = filters;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, logarithmicDepthBuffer: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x01030a, 1);
    host.appendChild(this.renderer.domElement);
    this.renderer.domElement.style.display = "block";

    this.camera = new THREE.PerspectiveCamera(55, 1, 1e-8, 2e5);
    this.camera.position.set(0, 6, 14);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    Object.assign(this.controls, { enableDamping: true, dampingFactor: 0.08, zoomSpeed: 1.6, rotateSpeed: 0.6,
      minDistance: 2e-7, maxDistance: 60000, zoomToCursor: true, screenSpacePanning: true });

    this.labelLayer = document.createElement("div");
    this.labelLayer.className = "pointer-events-none absolute inset-0 overflow-hidden";
    host.appendChild(this.labelLayer);
    this.sunLabel = document.createElement("div");
    this.sunLabel.className = "absolute whitespace-nowrap font-mono text-[11px] text-amber-200 [text-shadow:0_0_4px_#000]";
    this.sunLabel.textContent = "Sun";
    this.labelLayer.appendChild(this.sunLabel);

    // Stars.
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(data.xyz, 3));
    geo.setAttribute("aAttr", new THREE.BufferAttribute(data.attrs, 4, false));
    geo.setAttribute("aConf", new THREE.BufferAttribute(data.conf, 1, false));
    geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1e9);
    this.mat = new THREE.ShaderMaterial({
      vertexShader: VERT, fragmentShader: FRAG, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: {
        uOrigin: { value: new THREE.Vector3() }, uClassMask: { value: new Array(11).fill(1) }, uSourceMask: { value: [1, 1, 1, 1] },
        uClassColor: { value: classes.map((c) => new THREE.Color(c.color)) }, uSourceColor: { value: SRC_COLORS.map((c) => new THREE.Color(c)) },
        uReqFlags: { value: 0 }, uMaxDist: { value: 1e9 }, uMinConf: { value: 0 }, uColorMode: { value: 0 }, uUncharted: { value: 0 },
        uPixelRatio: { value: this.renderer.getPixelRatio() }, uExposure: { value: 6 }, uHide: { value: -1 }, uSelected: { value: -1 }, uTime: { value: 0 }, uMinAlpha: { value: 0.2 }, uCamDist: { value: 10 },
      },
    });
    this.stars = new THREE.Points(geo, this.mat);
    this.stars.frustumCulled = false;
    this.scene.add(this.stars);

    // Sun: a fixed-size marker glow so you can always find home, plus a true-size sphere (1 R_sun) that
    // shows when you zoom right in.
    this.sun = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture("#fff1d6"), blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: false, transparent: true }));
    this.sun.scale.set(0.03, 0.03, 1);
    this.scene.add(this.sun);
    this.sunBody = new THREE.Mesh(new THREE.SphereGeometry(R_SUN_PC, 48, 24), new THREE.MeshBasicMaterial({ color: "#fff4e0" }));
    this.scene.add(this.sunBody);

    // Distance rings + longitude spokes in the Galactic plane, centred on the Sun.
    for (const r of RINGS) {
      const pts = Array.from({ length: 257 }, (_, k) => { const a = (k / 256) * Math.PI * 2; return g2w(r * Math.cos(a), r * Math.sin(a), 0); });
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0x2dd4bf, transparent: true, opacity: 0.25, depthWrite: false }));
      line.userData.r = r;
      this.ringGroup.add(line);
      const lab = document.createElement("div");
      lab.className = "absolute font-mono text-[10px] text-teal-300/70 whitespace-nowrap";
      lab.textContent = r >= 1000 ? `${r / 1000} kpc` : `${r} pc`;
      this.labelLayer.appendChild(lab);
      this.ringLabels.push(lab);
    }
    for (let k = 0; k < 12; k++) {
      const a = (k / 12) * Math.PI * 2;
      const pts = [g2w(Math.cos(a) * 0.5, Math.sin(a) * 0.5, 0), g2w(Math.cos(a) * 16000, Math.sin(a) * 16000, 0)];
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0x2dd4bf, transparent: true, opacity: k % 3 === 0 ? 0.22 : 0.08, depthWrite: false }));
      line.userData.spoke = true;
      this.ringGroup.add(line);
    }
    this.scene.add(this.ringGroup);

    // Milky Way disk: an illustration for orientation at kpc scales (labelled as such in the UI).
    const diskMat = new THREE.MeshBasicMaterial({ map: spiralTexture(), transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide });
    this.disk = new THREE.Mesh(new THREE.PlaneGeometry(36000, 36000), diskMat);
    this.disk.rotation.x = -Math.PI / 2;
    this.scene.add(this.disk);

    this.applyFilters();
    this.resize();
    window.addEventListener("resize", this.resize);
    this.loop();
  }

  dispose() {
    cancelAnimationFrame(this.raf);
    window.removeEventListener("resize", this.resize);
    this.controls.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
    this.labelLayer.remove();
  }

  resize = () => {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    this.renderer.setSize(w, h, false);
    this.renderer.domElement.style.width = `${w}px`;
    this.renderer.domElement.style.height = `${h}px`;
    this.camera.aspect = w / Math.max(h, 1);
    this.camera.updateProjectionMatrix();
  };

  applyFilters() {
    const f = this.filters, u = this.mat.uniforms;
    u.uClassMask.value = f.classMask.map((b) => (b ? 1 : 0));
    u.uSourceMask.value = f.sourceMask.map((b) => (b ? 1 : 0));
    u.uReqFlags.value = f.requireFlags;
    u.uMaxDist.value = f.maxDist;
    u.uMinConf.value = f.minConf;
    u.uColorMode.value = f.colorMode;
    u.uUncharted.value = f.uncharted ? 1 : 0;
  }

  /** Whether object i passes the current filters (mirrors the shader). */
  visible(i: number): boolean {
    const a = this.data.attrs, f = this.filters;
    const cls = a[i * 4] & 15, src = a[i * 4] >> 4, fl = a[i * 4 + 1];
    const x = this.data.xyz[i * 3], y = this.data.xyz[i * 3 + 1], z = this.data.xyz[i * 3 + 2];
    if (!f.classMask[cls] || !f.sourceMask[src]) return false;
    if (Math.hypot(x, y, z) > f.maxDist) return false;
    if (f.requireFlags && !(fl & f.requireFlags)) return false;
    if (src === 1 && this.data.conf[i] / 255 < f.minConf) return false;
    return true;
  }

  galPos(i: number): THREE.Vector3 {
    return new THREE.Vector3(this.data.xyz[i * 3], this.data.xyz[i * 3 + 1], this.data.xyz[i * 3 + 2]);
  }

  /** World (render) position of a Galactic point under the current origin. */
  toWorld(g: THREE.Vector3): THREE.Vector3 {
    return g2w(g.x - this.origin.x, g.y - this.origin.y, g.z - this.origin.z);
  }

  /** Move the floating origin to `g` (Galactic pc) without moving the view. */
  private rebase(g: THREE.Vector3) {
    const shift = this.toWorld(g);
    this.camera.position.sub(shift);
    this.controls.target.sub(shift);
    this.origin.copy(g);
    this.mat.uniforms.uOrigin.value.copy(g);
  }

  /** Fly to a Galactic position, ending `dist` pc away from it. Google-Earth style hop for long trips. */
  flyTo(g: THREE.Vector3, dist: number, dur = 1800) {
    const prevTarget = this.controls.target.clone();
    this.rebase(g);
    const fromT = this.controls.target.clone();
    const fromD = this.camera.position.distanceTo(this.controls.target);
    const dir = this.camera.position.clone().sub(this.controls.target).normalize();
    const toDir = dir.clone().lerp(new THREE.Vector3(0, 0.45, 1).normalize(), 0.25).normalize();
    const travel = fromT.length();
    const hop = Math.max(0, Math.log(travel / Math.max(Math.min(fromD, dist), 1e-9)) * 0.45);
    void prevTarget;
    this.anim = { t0: performance.now(), dur, fromT, fromD, toD: dist, dir, toDir, hop: Math.min(hop, 6) };
  }

  flyToObject(i: number) {
    const sys = this.systemFor(i);
    let d = 1.5;
    if (sys) {
      const aMax = Math.max(...sys.planets.map((p) => p.a_au ?? 0.05), 0.05);
      d = (aMax * 3.2) / AU_PER_PC;
    } else {
      const r = Math.hypot(this.data.xyz[i * 3], this.data.xyz[i * 3 + 1], this.data.xyz[i * 3 + 2]);
      d = Math.min(2, Math.max(0.05, r * 0.03));
    }
    this.select(i);
    this.flyTo(this.galPos(i), d, 2200);
  }

  /** Galactic position (pc) of the point the camera orbits. */
  focusGal(): THREE.Vector3 {
    const t = this.controls.target;
    return new THREE.Vector3(this.origin.x + t.x, this.origin.y - t.z, this.origin.z + t.y);
  }

  /** Index of the visible star nearest Galactic point g, within maxDist pc (or -1). */
  nearestTo(g: THREE.Vector3, maxDist: number): number {
    const xyz = this.data.xyz;
    let best = -1, bd = maxDist * maxDist;
    for (let i = 0; i < this.data.n; i++) {
      const dx = xyz[i * 3] - g.x, dy = xyz[i * 3 + 1] - g.y, dz = xyz[i * 3 + 2] - g.z;
      const d2 = dx * dx + dy * dy + dz * dz;
      if (d2 < bd && this.visible(i)) { bd = d2; best = i; }
    }
    return best;
  }

  goHome() {
    this.select(-1);
    this.flyTo(new THREE.Vector3(), 12, 1800);
  }

  private systems = new Map<number, PlanetSystem>();
  setSystems(list: PlanetSystem[]) { for (const s of list) this.systems.set(s.i, s); }
  systemFor(i: number) { return this.systems.get(i) ?? null; }

  select(i: number) {
    this.selected = i;
    this.mat.uniforms.uSelected.value = i;
    this.buildSystem(i >= 0 ? this.systemFor(i) : null, i);
  }

  /** N-body track for the open system (from the backend engine), or null to use Kepler orbits. */
  private nbody: NbodyTrack | null = null;
  /** Which engine positioned the planets in the last frame. */
  orbitEngine: "kepler" | "nbody" = "kepler";

  /** Install (or extend) the N-body track for system i; ignored if another system is open. */
  setNbody(i: number, track: NbodyTrack | null) {
    if (i === this.selected) this.nbody = track;
  }

  private buildSystem(sys: PlanetSystem | null, i: number) {
    this.nbody = null;
    if (this.system) { this.scene.remove(this.system); this.system = null; }
    for (const p of this.systemPlanets) p.label.remove();
    this.systemPlanets = [];
    this.mat.uniforms.uHide.value = -1;
    if (!sys || i < 0) return;
    const grp = new THREE.Group();
    const pc = 1 / AU_PER_PC;
    // Star colour from its measured temperature (neutral grey when unmeasured); the sphere is
    // drawn to scale only when the radius is measured, otherwise just the glow marker.
    const teff = sys.st_teff;
    const starCol = teff == null ? "#cbd5e1" : teff > 7500 ? "#cad7ff" : teff > 6000 ? "#f8f7ff" : teff > 5200 ? "#fff4ea" : teff > 3700 ? "#ffd2a1" : "#ffad66";
    this.starRadiusKnown = sys.st_rad != null && sys.st_rad > 0;
    const starR = Math.max((this.starRadiusKnown ? sys.st_rad! : 0.1) * 0.00465047 * pc, 1e-9);
    if (this.starRadiusKnown) grp.add(new THREE.Mesh(new THREE.SphereGeometry(starR, 32, 16), new THREE.MeshBasicMaterial({ color: starCol })));
    const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture(starCol), blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: false, transparent: true }));
    glow.scale.set(0.06, 0.06, 1);
    grp.add(glow);
    // Real geometry: "towards the observer" is the actual direction from this star to the Sun.
    // Each orbit is tilted by its measured inclination about a horizontal line of nodes; the
    // rotation about the line of sight (node longitude) is unmeasurable and is the one free choice.
    const toSun = this.toWorld(new THREE.Vector3()).sub(this.toWorld(this.galPos(i)));
    if (toSun.lengthSq() < 1e-30) toSun.set(0, 0, 1);
    toSun.normalize();
    const node = new THREE.Vector3(toSun.z, 0, -toSun.x);
    if (node.lengthSq() < 1e-12) node.set(1, 0, 0);
    node.normalize();
    const up = new THREE.Vector3().crossVectors(toSun, node).normalize();
    this.sysBasis = { x: node, up, y: toSun };
    const B = this.sysBasis;
    const sky = (o: OrbitModel, xy: [number, number], v = new THREE.Vector3()) => {
      const [a, b, c] = toSky(o, xy);
      return v.copy(B.x).multiplyScalar(a * pc).addScaledVector(B.up, b * pc).addScaledVector(B.y, c * pc);
    };
    const orbits = sys.planets.map((p, k) => ({ p, orbit: orbitFor(p, sys.st_mass, k) }));
    // Habitable zone (Kopparapu et al. 2014), only when the star's T and R are measured; drawn in
    // the plane of the first orbit with a measured tilt (edge-on otherwise).
    this.hzDrawn = false;
    const hzr = habitableZone(sys.st_teff, sys.st_rad);
    if (hzr) {
      const ref = orbits.find((r) => r.orbit?.incl_known)?.orbit ?? orbits.find((r) => r.orbit)?.orbit;
      if (ref) {
        const ring = new THREE.RingGeometry(hzr[0] * pc, hzr[1] * pc, 128);
        const hz = new THREE.Mesh(ring, new THREE.MeshBasicMaterial({ color: 0x22c55e, transparent: true, opacity: 0.12, side: THREE.DoubleSide, depthWrite: false }));
        // Ring lies in its local XY plane: map local X -> node, local Y -> in-plane Y direction.
        const yDir = sky(ref, [0, 1]).divideScalar(pc);
        const normal = new THREE.Vector3().crossVectors(B.x, yDir).normalize();
        hz.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(B.x, yDir, normal));
        grp.add(hz);
        this.hzDrawn = true;
      }
    }
    this.unplottedPlanets = [];
    this.positionUnknown = [];
    orbits.forEach(({ p, orbit }) => {
      if (!orbit) { this.unplottedPlanets.push(p.name); return; }
      const short = p.name.replace(sys.host, "").trim() || p.name;
      const pts = (xy: [number, number][]) => new THREE.BufferGeometry().setFromPoints(xy.map((q) => sky(orbit, q)));
      if (orbit.shape === "band") {
        // e measured but not ω: only the range of distances is known.
        for (const r of [orbit.a * (1 - orbit.e), orbit.a * (1 + orbit.e)])
          grp.add(new THREE.Line(pts(circlePoints(r)), new THREE.LineBasicMaterial({ color: 0x7dd3fc, transparent: true, opacity: 0.25 })));
      } else {
        const line = new THREE.Line(pts(ellipsePoints(orbit)), orbit.shape === "measured"
          ? new THREE.LineBasicMaterial({ color: 0x7dd3fc, transparent: true, opacity: 0.45 })
          : new THREE.LineDashedMaterial({ color: 0x7dd3fc, transparent: true, opacity: 0.45, dashSize: orbit.a * pc * 0.06, gapSize: orbit.a * pc * 0.06 }));
        line.computeLineDistances();
        grp.add(line);
      }
      if (!hasPosition(orbit)) { this.positionUnknown.push(short); return; }
      // ±1σ arc of the predicted position (updated every frame as the uncertainty grows).
      const arc = new THREE.Line(new THREE.BufferGeometry().setFromPoints(Array.from({ length: 49 }, () => new THREE.Vector3())),
        new THREE.LineBasicMaterial({ color: 0xfcd34d, transparent: true, opacity: 0.9 }));
      arc.visible = false;
      grp.add(arc);
      const pr = Math.max((p.radius_re ?? 1) * 4.26352e-5 * pc, starR * 0.02);
      const teq = p.teq_k;
      // Colour by measured equilibrium temperature; grey when the archive has none.
      const col = teq == null ? 0x94a3b8 : teq > 1000 ? 0xf97316 : teq > 400 ? 0xeab308 : teq > 200 ? 0x22d3ee : 0x93c5fd;
      const mesh = new THREE.Mesh(new THREE.SphereGeometry(pr, 16, 8), new THREE.MeshBasicMaterial({ color: col }));
      const dot = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture("#ffffff"), color: col, depthWrite: false, sizeAttenuation: false,
        blending: THREE.AdditiveBlending, transparent: true }));
      dot.scale.set(0.012, 0.012, 1);
      mesh.add(dot);
      grp.add(mesh);
      const label = document.createElement("div");
      label.className = "absolute font-mono text-[10px] text-sky-200 whitespace-nowrap";
      label.textContent = short;
      this.labelLayer.appendChild(label);
      this.systemPlanets.push({ mesh, orbit, arc, label, name: p.name });
    });
    this.simStart = { ms: performance.now(), jd: jdNow() };
    this.system = grp;
    this.scene.add(grp);
    this.mat.uniforms.uHide.value = i; // the system's own star is drawn as a sphere at the origin
  }

  /** Screen-space pick of the brightest visible star near (x, y) CSS pixels. */
  pick(x: number, y: number, radius = 14): PickResult | null {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    const m = new THREE.Matrix4().multiplyMatrices(this.camera.projectionMatrix, this.camera.matrixWorldInverse);
    const e = m.elements, o = this.origin, xyz = this.data.xyz, att = this.data.attrs;
    let best = -1, bestScore = -Infinity, bx = 0, by = 0;
    const cam = this.camera.position;
    for (let i = 0; i < this.data.n; i++) {
      if (i === this.selected && this.system) continue;
      const gx = xyz[i * 3] - o.x, gy = xyz[i * 3 + 1] - o.y, gz = xyz[i * 3 + 2] - o.z;
      const wx = gx, wy = gz, wz = -gy;
      const cw = e[3] * wx + e[7] * wy + e[11] * wz + e[15];
      if (cw <= 0) continue;
      const sx = ((e[0] * wx + e[4] * wy + e[8] * wz + e[12]) / cw * 0.5 + 0.5) * w;
      const sy = (1 - ((e[1] * wx + e[5] * wy + e[9] * wz + e[13]) / cw * 0.5 + 0.5)) * h;
      const dx = sx - x, dy = sy - y;
      const d2 = dx * dx + dy * dy;
      if (d2 > radius * radius) continue;
      if (!this.visible(i)) continue;
      const dc = Math.hypot(wx - cam.x, wy - cam.y, wz - cam.z);
      const mApp = att[i * 4 + 2] / 8 - 8 + 5 * Math.log10(Math.max(dc, 1e-7) / 10);
      const score = -mApp - d2 / 40;
      if (score > bestScore) { bestScore = score; best = i; bx = sx; by = sy; }
    }
    return best >= 0 ? { i: best, screen: [bx, by] } : null;
  }

  private loop = () => {
    this.raf = requestAnimationFrame(this.loop);
    this.clock.getDelta();
    if (this.paused) return;
    const now = performance.now();
    if (this.anim) {
      const a = this.anim;
      const t = Math.min(1, (now - a.t0) / a.dur);
      const s = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
      const target = a.fromT.clone().multiplyScalar(1 - s);
      const logD = Math.log(a.fromD) * (1 - s) + Math.log(a.toD) * s + a.hop * Math.sin(Math.PI * s);
      const dir = a.dir.clone().lerp(a.toDir, s).normalize();
      this.controls.target.copy(target);
      this.camera.position.copy(target).addScaledVector(dir, Math.exp(logD));
      if (t >= 1) this.anim = null;
    }
    this.controls.update();
    const camDist = this.camera.position.distanceTo(this.controls.target);
    // Auto-exposure: as you zoom out, fainter stars fade so the large-scale structure stays readable.
    this.mat.uniforms.uExposure.value = this.exposureFor(camDist);
    this.mat.uniforms.uCamDist.value = camDist;
    this.mat.uniforms.uMinAlpha.value = THREE.MathUtils.clamp(0.22 - 0.07 * Math.log10(Math.max(camDist, 1e-6) / 10), 0.035, 0.22);
    this.mat.uniforms.uTime.value = now / 1000;

    const sunW = this.toWorld(new THREE.Vector3());
    this.sun.position.copy(sunW);
    this.sunBody.position.copy(sunW);
    this.ringGroup.position.copy(sunW);
    const H = Math.max(this.host.clientHeight, 1);
    this.sunScreen = { d: sunW.distanceTo(this.camera.position), px: (0.03 * H) / (2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2))) };
    const gcW = this.toWorld(new THREE.Vector3(GC_DIST, 0, 0));
    this.disk.position.copy(gcW);
    const distFromSunToFocus = this.origin.length() + this.controls.target.length();
    const scale = Math.max(camDist, 1e-9);
    (this.disk.material as THREE.MeshBasicMaterial).opacity = THREE.MathUtils.clamp((Math.log10(scale) - 2.6) / 1.2, 0, 0.85);
    for (const obj of this.ringGroup.children) {
      const line = obj as THREE.Line;
      const mat = line.material as THREE.LineBasicMaterial;
      if (line.userData.spoke) { mat.opacity = THREE.MathUtils.clamp((Math.log10(scale) + 0.5) / 2, 0, 1) * 0.15; continue; }
      const r = line.userData.r as number;
      const rel = Math.log10(r / scale);
      mat.opacity = THREE.MathUtils.clamp(1 - Math.abs(rel + 0.1) / 1.1, 0, 1) * 0.35;
    }
    let orbitJd: number | null = null;
    if (this.system) {
      // One fixed rate for every system, tied to the wall clock (frame rate cannot change it).
      orbitJd = this.simStart.jd + ((now - this.simStart.ms) / 1000) * DAYS_PER_SECOND;
      const B = this.sysBasis, pc = 1 / AU_PER_PC;
      const put = (v: THREE.Vector3, xy: [number, number], o: OrbitModel) => {
        const [a, b, c] = toSky(o, xy);
        v.copy(B.x).multiplyScalar(a * pc).addScaledVector(B.up, b * pc).addScaledVector(B.y, c * pc);
      };
      const put3 = (v: THREE.Vector3, [a, b, c]: [number, number, number]) =>
        v.copy(B.x).multiplyScalar(a * pc).addScaledVector(B.up, b * pc).addScaledVector(B.y, c * pc);
      // N-body track (planet-planet gravity) when loaded and covering this date; else Kepler.
      const track = this.nbody && covers(this.nbody, orbitJd) ? this.nbody : null;
      this.orbitEngine = track ? "nbody" : "kepler";
      for (const p of this.systemPlanets) {
        const M = meanAnomaly(p.orbit, orbitJd);
        const nb = track ? trackPosition(track, p.name, orbitJd) : null;
        if (nb) put3(p.mesh.position, nb);
        else put(p.mesh.position, positionAtM(p.orbit, M), p.orbit);
        // ±1σ from the archive's ephemeris errors (the same measurement uncertainty in both engines).
        const sig = phaseSigma(p.orbit, orbitJd);
        p.arc.visible = Number.isFinite(sig) && sig > 0.002;
        if (p.arc.visible) {
          const span = Math.min(sig, Math.PI);
          const attr = p.arc.geometry.getAttribute("position") as THREE.BufferAttribute;
          const v = new THREE.Vector3();
          const dt = (span / (2 * Math.PI)) * p.orbit.P;
          for (let k = 0; k < attr.count; k++) {
            const f = -1 + (2 * k) / (attr.count - 1);
            const q = nb ? trackPosition(track!, p.name, Math.min(Math.max(orbitJd + f * dt, track!.t0), trackEnd(track!))) : null;
            if (q) put3(v, q);
            else put(v, positionAtM(p.orbit, M + f * span), p.orbit);
            attr.setXYZ(k, v.x, v.y, v.z);
          }
          attr.needsUpdate = true;
        }
      }
      this.system.position.copy(this.toWorld(this.galPos(this.selected)));
      this.system.visible = camDist < 0.05;
    }
    this.renderer.render(this.scene, this.camera);
    if (now - this.lastLabel > 120) { this.lastLabel = now; this.updateLabels(camDist); }
    this.onFrame?.({ camDist, focusDistFromSun: distFromSunToFocus, orbitJd: this.system?.visible ? orbitJd : null, engine: this.orbitEngine });
  };

  private project(v: THREE.Vector3): [number, number, boolean] {
    const p = v.clone().project(this.camera);
    const w = this.host.clientWidth, h = this.host.clientHeight;
    return [(p.x * 0.5 + 0.5) * w, (1 - (p.y * 0.5 + 0.5)) * h, p.z < 1 && p.z > -1];
  }

  private sunScreen = { d: 1, px: 1 };

  private updateLabels(camDist: number) {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    {
      const [x, y, ok] = this.project(this.sun.position);
      const show = ok && x > 0 && x < w && y > 0 && y < h && this.sunScreen.d < 60;
      this.sunLabel.style.display = show ? "block" : "none";
      this.sunLabel.style.transform = `translate(${x + 6 + this.sunScreen.px / 2}px, ${y - 7}px)`;
    }
    // Ring labels.
    this.ringGroup.children.forEach((obj, k) => {
      const lab = this.ringLabels[k];
      if (!lab) return;
      const line = obj as THREE.Line;
      const op = (line.material as THREE.LineBasicMaterial).opacity;
      const r = line.userData.r as number;
      const [x, y, ok] = this.project(this.ringGroup.position.clone().add(g2w(r * Math.cos(0.35), r * Math.sin(0.35), 0)));
      lab.style.display = ok && op > 0.05 && x > 0 && x < w && y > 0 && y < h ? "block" : "none";
      lab.style.transform = `translate(${x + 4}px, ${y}px)`;
      lab.style.opacity = String(Math.min(1, op * 3));
    });
    // Planet labels.
    for (const p of this.systemPlanets) {
      const [x, y, ok] = this.project(p.mesh.getWorldPosition(new THREE.Vector3()));
      const show = ok && !!this.system?.visible && camDist < (p.orbit.a / AU_PER_PC) * 60;
      p.label.style.display = show ? "block" : "none";
      p.label.style.transform = `translate(${x + 8}px, ${y - 6}px)`;
    }
    // Star labels: brightest-looking named objects in view, without overlaps.
    const cam = this.camera.position;
    const cands: { o: NamedObject; x: number; y: number; m: number }[] = [];
    const v = new THREE.Vector3();
    for (const o of this.named) {
      if (!this.visible(o.i) || (o.i === this.selected && this.system)) continue;
      v.copy(this.toWorld(this.galPos(o.i)));
      const dc = v.distanceTo(cam);
      const mApp = this.data.attrs[o.i * 4 + 2] / 8 - 8 + 5 * Math.log10(Math.max(dc, 1e-7) / 10);
      const bonus = (o.f & FLAG.planet_host ? 1.5 : 0) + (o.f & FLAG.spherex ? 2.5 : 0) + (o.n.length < 14 ? 0.8 : 0);
      // Prefer objects near the point you are looking at, so zooming in reveals the local names.
      const dTarget = v.distanceTo(this.controls.target);
      const score = mApp - bonus + 4 * Math.log10(Math.max(dTarget / Math.max(camDist, 1e-9), 0.25));
      if (score > this.exposureFor(camDist) + 1) continue;
      const [x, y, ok] = this.project(v);
      if (!ok || x < 0 || x > w || y < 0 || y > h) continue;
      cands.push({ o, x, y, m: score });
    }
    cands.sort((a, b) => a.m - b.m);
    const placed: [number, number][] = [];
    let k = 0;
    for (const c of cands) {
      if (k >= (camDist > 400 ? 14 : 40)) break;
      if (placed.some(([px, py]) => Math.abs(px - c.x) < 90 && Math.abs(py - c.y) < 16)) continue;
      placed.push([c.x, c.y]);
      const el = this.labelPool[k] ?? this.newLabel();
      el.textContent = c.o.n;
      el.dataset.i = String(c.o.i);
      el.style.display = "block";
      el.style.transform = `translate(${c.x + 9}px, ${c.y - 7}px)`;
      el.style.color = this.classes[c.o.c]?.color ?? "#cbd5e1";
      el.title = `${c.o.n} · ${fmtDist(c.o.d)}`;
      k++;
    }
    for (let j = k; j < this.labelPool.length; j++) this.labelPool[j].style.display = "none";
  }

  private newLabel(): HTMLDivElement {
    const el = document.createElement("div");
    el.className = "pointer-events-auto absolute cursor-pointer whitespace-nowrap font-mono text-[11px] opacity-90 hover:opacity-100 hover:underline [text-shadow:0_0_4px_#000]";
    el.addEventListener("click", (ev) => { ev.stopPropagation(); this.onLabelClick?.(Number(el.dataset.i)); });
    this.labelLayer.appendChild(el);
    this.labelPool.push(el);
    return el;
  }
}

function glowTexture(color: string): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d")!;
  const grd = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grd.addColorStop(0, "#ffffff");
  grd.addColorStop(0.2, color);
  grd.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = grd;
  g.fillRect(0, 0, 64, 64);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/** Procedural barred-spiral disk (illustration only) seen face-on; 36 kpc across. */
function spiralTexture(): THREE.Texture {
  const N = 2048, c = document.createElement("canvas");
  c.width = c.height = N;
  const g = c.getContext("2d")!;
  g.fillStyle = "#000";
  g.fillRect(0, 0, N, N);
  const cx = N / 2, cy = N / 2, kpc = N / 36;
  const bulge = g.createRadialGradient(cx, cy, 0, cx, cy, 3.2 * kpc);
  bulge.addColorStop(0, "rgba(255,226,170,0.95)");
  bulge.addColorStop(0.5, "rgba(255,190,120,0.35)");
  bulge.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = bulge;
  g.fillRect(0, 0, N, N);
  let seed = 42;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const arms = 4, pitch = Math.tan((12 * Math.PI) / 180);
  for (let k = 0; k < 60000; k++) {
    const arm = k % arms;
    const r = (2.2 + rnd() * 13.5) * kpc;
    const th0 = Math.log(r / (2.2 * kpc)) / pitch + (arm * 2 * Math.PI) / arms + 0.4;
    const th = th0 + (rnd() - 0.5) * 0.45;
    const rr = r * (1 + (rnd() - 0.5) * 0.12);
    const x = cx + rr * Math.cos(th), y = cy + rr * Math.sin(th);
    const s = rnd() * 2.2 + 0.4;
    const hot = rnd() < 0.3;
    g.fillStyle = hot ? `rgba(150,180,255,${0.25 + rnd() * 0.35})` : `rgba(255,225,190,${0.12 + rnd() * 0.25})`;
    g.fillRect(x, y, s, s);
  }
  for (let k = 0; k < 20000; k++) { // diffuse disk
    const r = Math.sqrt(rnd()) * 16 * kpc, th = rnd() * Math.PI * 2;
    g.fillStyle = `rgba(220,210,255,${0.05 + rnd() * 0.08})`;
    g.fillRect(cx + r * Math.cos(th), cy + r * Math.sin(th), 1.5, 1.5);
  }
  // Bar.
  g.save();
  g.translate(cx, cy);
  g.rotate((-27 * Math.PI) / 180);
  const bar = g.createRadialGradient(0, 0, 0, 0, 0, 4.5 * kpc);
  bar.addColorStop(0, "rgba(255,215,160,0.6)");
  bar.addColorStop(1, "rgba(0,0,0,0)");
  g.scale(1, 0.35);
  g.fillStyle = bar;
  g.beginPath();
  g.arc(0, 0, 4.5 * kpc, 0, Math.PI * 2);
  g.fill();
  g.restore();
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  // Plane is centred on the Galactic centre; texture +u maps to world +x. The Sun sits at -8.2 kpc (toward -x from the GC).
  return t;
}
