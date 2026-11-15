import { useId } from "react";

/**
 * A hand-painted, dry-brush underline (inline SVG, no image asset): thick ragged start tapering to
 * a thin tail, with streaky gaps where the brush runs dry. Sweeps in left → right on load
 * (skipped for reduced motion). Place inside a `relative` inline element.
 */
export function BrushUnderline({ color = "#ff1f4b", className = "" }: { color?: string; className?: string }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg
      aria-hidden
      viewBox="0 0 600 34"
      preserveAspectRatio="none"
      className={`brush-sweep pointer-events-none absolute ${className}`}
    >
      <defs>
        {/* rough, torn edges */}
        <filter id={`rough-${id}`} x="-5%" y="-60%" width="110%" height="220%">
          <feTurbulence type="fractalNoise" baseFrequency="0.035 0.6" numOctaves="3" seed="7" result="n" />
          <feDisplacementMap in="SourceGraphic" in2="n" scale="7" xChannelSelector="R" yChannelSelector="G" result="d" />
          {/* dry-brush streaks: long horizontal noise thresholded into gaps */}
          <feTurbulence type="fractalNoise" baseFrequency="0.006 0.45" numOctaves="2" seed="3" result="s" />
          <feColorMatrix in="s" type="matrix"
            values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -2.6 2.15" result="mask" />
          <feComposite in="d" in2="mask" operator="in" />
        </filter>
      </defs>
      <g filter={`url(#rough-${id})`} fill={color}>
        {/* main stroke: heavy on the left, tapering to a point on the right */}
        <path d="M6 9 C 60 6, 140 8, 250 9 S 470 12, 596 15 L 597 18 C 470 20, 330 22, 200 23 S 50 26, 8 25 Z" />
        {/* a second, thinner pass slightly below, like the bristles separating */}
        <path d="M14 24 C 120 25, 260 24, 420 22 L 560 21 L 560 23 C 420 25, 250 28, 16 29 Z" opacity="0.85" />
        {/* thin top bristle line */}
        <path d="M30 5 C 160 3, 330 5, 520 9 L 520 10.5 C 330 7, 160 6, 30 7.5 Z" opacity="0.7" />
      </g>
    </svg>
  );
}
