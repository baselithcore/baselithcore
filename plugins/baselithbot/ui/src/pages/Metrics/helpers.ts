import {
  CategoryScale,
  Chart as ChartJS,
  Filler,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
} from 'chart.js';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Filler);

export type SortKey = 'tokens' | 'cost' | 'events';

export const SORT_LABELS: Record<SortKey, string> = {
  tokens: 'Tokens',
  cost: 'Cost',
  events: 'Events',
};

export const ACCENTS = ['teal', 'violet', 'cyan', 'amber', 'rose'] as const;

export type ModelRow = [string, { events: number; tokens: number; cost_usd: number }];
