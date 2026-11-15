import { useId, type ReactNode } from "react";

/**
 * Hand-drawn margin sketches for the "Spot the Mover" card: pencil line drawings with red-pen
 * notes, like a designer's sketchbook page. Pure inline SVG; a light displacement filter makes
 * the strokes wobble as if drawn by hand. Decorative only (aria-hidden, no pointer events).
 */
const PENCIL = "rgb(229 231 235 / 0.6)";
const HATCH = "rgb(229 231 235 / 0.28)";
const INK = "rgb(255 255 255 / 0.9)";
const hand = { fontFamily: "var(--font-hand)" };

function Sketch({ viewBox, className = "", children }: { viewBox: string; className?: string; children: ReactNode }) {
  const id = "pencil" + useId().replace(/:/g, "");
  return (
    <svg aria-hidden viewBox={viewBox} className={`pointer-events-none overflow-visible ${className}`}
      fill="none" strokeLinecap="round" strokeLinejoin="round">
      <defs>
        <filter id={id} x="-5%" y="-5%" width="110%" height="110%">
          <feTurbulence type="fractalNoise" baseFrequency="0.04" numOctaves="2" seed="4" result="n" />
          <feDisplacementMap in="SourceGraphic" in2="n" scale="2.2" xChannelSelector="R" yChannelSelector="G" />
        </filter>
      </defs>
      <g filter={`url(#${id})`}>{children}</g>
    </svg>
  );
}

/** Pencil cuboid: front face (x, y, w, h) with a top face and a hatched side face. */
function Cuboid({ x, y, w, h, d = 22, rise = 16 }: { x: number; y: number; w: number; h: number; d?: number; rise?: number }) {
  const hatch = [];
  for (let i = 6; i < h + rise; i += 7) {
    const y1 = y + i, y2 = y + i - rise - 6;
    hatch.push(<path key={i} d={`M${x + w + 2} ${Math.min(y1, y + h - 1)} L${x + w + d - 2} ${Math.max(y2, y - rise + 2)}`} />);
  }
  return (
    <>
      <g stroke={HATCH} strokeWidth="1">{hatch}</g>
      <g stroke={PENCIL} strokeWidth="1.4">
        <path d={`M${x} ${y} H${x + w} V${y + h} H${x} Z`} />
        <path d={`M${x} ${y} L${x + d} ${y - rise} H${x + w + d} L${x + w} ${y}`} />
        <path d={`M${x + w + d} ${y - rise} V${y + h - rise} L${x + w} ${y + h}`} />
        {/* overshoot strokes, the way a quick sketch overruns its corners */}
        <path d={`M${x - 6} ${y} H${x + 4}`} strokeWidth="1" />
        <path d={`M${x + w} ${y + h} L${x + w + 7} ${y + h + 2}`} strokeWidth="1" />
      </g>
    </>
  );
}

const Arrowhead = ({ x, y, a, b }: { x: number; y: number; a: [number, number]; b: [number, number] }) => (
  <path d={`M${a[0]} ${a[1]} L${x} ${y} L${b[0]} ${b[1]}`} stroke={INK} strokeWidth="1.8" />
);

const STARS: [number, number][] = [[45, 70], [70, 98], [100, 64], [116, 106], [56, 112], [30, 88]];

/** Two image frames months apart: the same stars, except one that has moved. */
export function FramesSketch({ className = "" }: { className?: string }) {
  return (
    <Sketch viewBox="0 0 470 160" className={className}>
      <Cuboid x={20} y={50} w={112} h={72} />
      <Cuboid x={250} y={50} w={112} h={72} />
      <g fill={PENCIL} stroke="none">
        {STARS.map(([sx, sy]) => <circle key={`a${sx}`} cx={sx} cy={sy} r="1.8" />)}
        {STARS.map(([sx, sy]) => <circle key={`b${sx}`} cx={sx + 230} cy={sy} r="1.8" />)}
        <circle cx={86} cy={82} r="2.4" />
        <circle cx={330} cy={75} r="2.4" />
      </g>
      {/* red pen: circle the mover in both frames, ghost of where it was */}
      <g stroke={INK} strokeWidth="1.6">
        <path d="M78 80 C 78 72, 95 72, 95 81 S 80 92, 77 82 L 79 77" />
        <path d="M322 73 C 322 65, 339 65, 339 74 S 324 85, 321 75 L 323 70" />
        <circle cx={316} cy={82} r="5" strokeDasharray="2 3" strokeWidth="1.2" />
        <path d="M321 80 L 326 77" strokeWidth="1.2" />
        {/* months later */}
        <path d="M156 32 C 185 12, 222 12, 250 30" />
      </g>
      <Arrowhead x={250} y={30} a={[240, 21]} b={[239, 32]} />
      <text x={201} y={60} textAnchor="middle" fontSize="15" fill={INK} style={hand}>months</text>
      <text x={201} y={76} textAnchor="middle" fontSize="15" fill={INK} style={hand}>later</text>
      {/* the mover! */}
      <path d="M408 46 C 392 50, 360 52, 343 66" stroke={INK} strokeWidth="1.6" />
      <Arrowhead x={343} y={66} a={[346, 56]} b={[353, 66]} />
      <text x={398} y={40} fontSize="17" fill={INK} style={hand}>the mover!</text>
      {/* time arrow */}
      <path d="M24 146 C 120 142, 250 148, 368 144" stroke={INK} strokeWidth="1.4" />
      <Arrowhead x={368} y={144} a={[359, 139]} b={[359, 150]} />
      <text x={378} y={149} fontSize="14" fill={INK} style={hand}>TIME</text>
    </Sketch>
  );
}

/** "start here!" with a looping arrow, sits to the right of the Play button. */
export function StartHereNote({ className = "" }: { className?: string }) {
  return (
    <Sketch viewBox="0 0 170 56" className={className}>
      <path d="M74 32 C 60 44, 52 22, 42 34 S 24 46, 10 40" stroke={INK} strokeWidth="1.7" />
      <Arrowhead x={10} y={40} a={[19, 33]} b={[20, 45]} />
      <text x={70} y={22} fontSize="18" fill={INK} style={hand}>start here!</text>
    </Sketch>
  );
}

/** Arrow up to the Leaderboards link, with a little pencil podium. */
export function BoardSketch({ className = "" }: { className?: string }) {
  return (
    <Sketch viewBox="0 0 240 84" className={className}>
      <path d="M62 64 C 42 56, 30 34, 38 6" stroke={INK} strokeWidth="1.7" />
      <Arrowhead x={38} y={6} a={[31, 15]} b={[45, 14]} />
      <text x={70} y={68} fontSize="16" fill={INK} style={hand}>beat the board</text>
      <Cuboid x={176} y={52} w={18} h={24} d={6} rise={5} />
      <Cuboid x={196} y={40} w={18} h={36} d={6} rise={5} />
      <Cuboid x={216} y={58} w={16} h={18} d={6} rise={5} />
      <path d="M205 22 L 207 28 L 213 28 L 208 32 L 210 38 L 205 34 L 200 38 L 202 32 L 197 28 L 203 28 Z" stroke={INK} strokeWidth="1.3" />
    </Sketch>
  );
}

/** Wavy arrow from the text column into the interactive box. */
export function BlinkArrow({ className = "" }: { className?: string }) {
  return (
    <Sketch viewBox="0 0 200 80" className={className}>
      <text x={14} y={24} fontSize="17" fill={INK} style={hand}>blink to find it</text>
      <path d="M18 46 C 58 66, 92 26, 132 46 S 178 58, 196 44" stroke={INK} strokeWidth="1.7" />
      <Arrowhead x={196} y={44} a={[185, 41]} b={[189, 53]} />
    </Sketch>
  );
}

/** Note above the interactive box. */
export function BoxNote({ className = "" }: { className?: string }) {
  return (
    <Sketch viewBox="0 0 200 38" className={className}>
      <text x={0} y={18} fontSize="17" fill={INK} style={hand}>your sky goes here</text>
      <path d="M156 12 C 174 10, 184 18, 184 34" stroke={INK} strokeWidth="1.6" />
      <Arrowhead x={184} y={34} a={[178, 26]} b={[190, 27]} />
    </Sketch>
  );
}
