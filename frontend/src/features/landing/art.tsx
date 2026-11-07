// Brand mark and decorative illustrations for the landing page. Plain SVG, no image files.
import { useId } from "react";

/** The Mehmanawazi app icon: a cloche over an "M" on a rounded orange square. */
export function Logo({ size = 32, className }: { size?: number; className?: string }) {
  const id = useId();
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 64 64" role="img" aria-label="Mehmanawazi">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#FF6A3D" />
          <stop offset="1" stopColor="#F2300F" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" fill={`url(#${id})`} />
      <g fill="#fff">
        <circle cx="31" cy="15.6" r="2.3" />
        <rect x="30.1" y="16.5" width="1.8" height="3" />
        <path d="M17.4 30.6c1.6-6.6 7.1-11.3 13.6-11.3s12 4.7 13.6 11.3z" />
        <path d="M12.4 32.4c0-.7.6-1.2 1.3-1.2h34.6c.7 0 1.3.5 1.3 1.2s-.6 1.2-1.3 1.2H13.7c-.7 0-1.3-.5-1.3-1.2z" />
      </g>
      <path d="M22.6 21.9c-1.8 1.4-3 3.1-3.7 5" stroke="#FF6A3D" strokeWidth="1.3" strokeLinecap="round" fill="none" opacity=".55" />
      <path d="M19 50V40.2c0-2.3 1.5-3.6 3.7-3.6 3.6 0 6.3 3.1 9.3 7.4 3-4.3 5.7-7.4 9.3-7.4 2.2 0 3.7 1.3 3.7 3.6V50"
        stroke="#fff" strokeWidth="5.6" strokeLinecap="round" strokeLinejoin="round" fill="none" />
      <g stroke="#fff" strokeWidth="1.5" strokeLinecap="round" fill="none">
        <path d="M43.5 9.5c-1.4 1.6-.4 2.8.4 3.9.9 1.2 1 2.6-.4 4" />
        <path d="M47.3 10.8c-1.2 1.4-.3 2.5.4 3.4.8 1.1.9 2.3-.3 3.6" />
      </g>
    </svg>
  );
}

/** A latte on a saucer, with steam that drifts (CSS: .lp-steam). */
export function CoffeeCup({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 220 190" aria-hidden="true">
      <ellipse cx="110" cy="166" rx="96" ry="18" fill="#F1ECE8" />
      <ellipse cx="110" cy="163" rx="64" ry="10" fill="#E7DFD9" />
      <path d="M168 92c26-2 32 36 2 46" stroke="#F3E3DC" strokeWidth="12" strokeLinecap="round" fill="none" />
      <path d="M42 84h136v18c0 44-30 62-68 62S42 146 42 102z" fill="#FFFFFF" />
      <path d="M42 84h136v18c0 44-30 62-68 62S42 146 42 102z" fill="none" stroke="#F3E3DC" strokeWidth="3" />
      <ellipse cx="110" cy="84" rx="68" ry="14" fill="#FFFFFF" stroke="#F3E3DC" strokeWidth="3" />
      <ellipse cx="110" cy="85" rx="60" ry="10.5" fill="#7A3E22" />
      <ellipse cx="110" cy="85" rx="50" ry="8" fill="#C17A4A" />
      <path d="M110 92c-8-4-15-7-15-11 0-3 3-5 6-5 4 0 7 2 9 5 2-3 5-5 9-5 3 0 6 2 6 5 0 4-7 7-15 11z" fill="#FBEFE6" />
      <g className="lp-steam" stroke="#FF563C" strokeWidth="4" strokeLinecap="round" fill="none" opacity=".35">
        <path d="M90 62c-8-9 6-14-2-26" />
        <path d="M112 58c-8-10 7-15-1-30" />
        <path d="M134 62c-8-9 6-14-2-26" />
      </g>
    </svg>
  );
}

/** A cloche on a plate, the hospitality symbol from the logo. */
export function Cloche({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 200 140" aria-hidden="true">
      <ellipse cx="100" cy="122" rx="92" ry="12" fill="#FFE8E4" />
      <rect x="14" y="104" width="172" height="10" rx="5" fill="#FF563C" />
      <path d="M30 102c4-38 34-64 70-64s66 26 70 64z" fill="#FF563C" />
      <path d="M52 78c8-14 22-24 38-27" stroke="#fff" strokeWidth="6" strokeLinecap="round" fill="none" opacity=".45" />
      <circle cx="100" cy="30" r="9" fill="#FF563C" />
      <g className="lp-steam" stroke="#FF563C" strokeWidth="4" strokeLinecap="round" fill="none" opacity=".45">
        <path d="M150 34c-6-7 5-11-1-21" />
        <path d="M164 38c-6-7 5-11-1-21" />
      </g>
    </svg>
  );
}

/** A potted plant for the café mood. */
export function Plant({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 120 170" aria-hidden="true">
      <path d="M60 112C58 80 40 60 18 52c10 24 22 44 42 60z" fill="#3E8E5E" />
      <path d="M60 112c2-36 20-58 44-68-8 28-22 50-44 68z" fill="#2F7D4F" />
      <path d="M60 110C54 72 58 40 66 14c10 30 6 62-6 96z" fill="#4FA772" />
      <path d="M60 112c-14-18-34-26-52-24 14 16 30 24 52 24z" fill="#2F7D4F" />
      <path d="M32 108h56l-8 54H40z" fill="#FF563C" />
      <rect x="28" y="104" width="64" height="12" rx="4" fill="#E8441F" />
    </svg>
  );
}

/** A table tent with a QR, used in "Scan". */
export function TableTent({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 140 150" aria-hidden="true">
      <ellipse cx="70" cy="140" rx="62" ry="8" fill="#F1ECE8" />
      <path d="M22 136 46 12h48l24 124z" fill="#FFFFFF" stroke="#F3E3DC" strokeWidth="3" />
      <rect x="44" y="36" width="52" height="52" rx="6" fill="#111" />
      <rect x="49" y="41" width="42" height="42" rx="3" fill="#fff" />
      <g fill="#111">
        <rect x="52" y="44" width="12" height="12" rx="2" /><rect x="76" y="44" width="12" height="12" rx="2" /><rect x="52" y="68" width="12" height="12" rx="2" />
        <rect x="70" y="62" width="5" height="5" /><rect x="78" y="70" width="5" height="5" /><rect x="70" y="76" width="5" height="5" /><rect x="84" y="62" width="4" height="4" /><rect x="84" y="78" width="4" height="4" />
      </g>
      <rect x="48" y="98" width="44" height="7" rx="3.5" fill="#FF563C" />
      <rect x="54" y="111" width="32" height="5" rx="2.5" fill="#E7DFD9" />
    </svg>
  );
}
