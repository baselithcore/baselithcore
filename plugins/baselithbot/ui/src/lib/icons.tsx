import type { SVGProps } from 'react';

type IconProps = Omit<SVGProps<SVGSVGElement>, 'children'> & {
  path: string;
  size?: number;
};

export function Icon({ path, size = 18, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <path d={path} />
    </svg>
  );
}

export const paths = {
  activity: 'M22 12h-4l-3 9L9 3l-3 9H2',
  bolt: 'M13 2 3 14h9l-1 8 10-12h-9l1-8z',
  bot: 'M12 8V4M5 10v8a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-8M8 14h.01M16 14h.01M9 18h6M3 10h18',
  box: 'M21 8 12 3 3 8v8l9 5 9-5z M3 8l9 5 9-5 M12 13v8',
  cable: 'M4 9v10a2 2 0 0 0 2 2h4 M14 3v10a2 2 0 0 0 2 2h4 M4 3h4v4H4zM16 21h4v-4h-4z',
  clock: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zM12 6v6l4 2',
  heart:
    'M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z',
  messages: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
  play: 'M5 3l14 9-14 9V3z',
  radar: 'M19.1 4.9A10 10 0 1 1 5 19 M12 2v10l5 3',
  waypoints:
    'M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1',
  terminal: 'M4 17l6-6-6-6 M12 19h8',
  refresh: 'M23 4v6h-6 M1 20v-6h6 M3.5 9A9 9 0 0 1 18 5.3L23 10 M20.5 15A9 9 0 0 1 6 18.7L1 14',
  plus: 'M12 5v14 M5 12h14',
  trash:
    'M3 6h18 M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2 M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6',
  shield: 'M12 2 4 5v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V5z',
  shieldOff: 'M20 13c0 5-4.5 9-8 9-1.8-.5-3.4-1.5-4.6-2.8 M4 13V5l5-2 M2 2l20 20',
  zap: 'M13 2 3 14h9l-1 8 10-12h-9l1-8z',
  copy: 'M20 9h-9a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2v-9a2 2 0 0 0-2-2zM5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1',
  sparkles: 'M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z',
  coin: 'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zM12 7v10M9 9h4.5a2 2 0 0 1 0 4H9v4',
  menu: 'M4 7h16M4 12h16M4 17h16',
  check: 'M5 12l5 5 10-11',
  x: 'M6 6l12 12M18 6L6 18',
} as const;

export type IconName = keyof typeof paths;
