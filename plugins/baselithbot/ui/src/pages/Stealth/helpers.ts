import type { StealthConfig } from '../../lib/api';
import type { IconName } from '../../lib/icons';

export type StealthToggleKey = 'enabled' | 'rotate_user_agent' | 'mask_webdriver';

export type StealthToggleSpec = {
  key: StealthToggleKey;
  label: string;
  description: string;
  icon: IconName;
  accent: 'teal' | 'amber' | 'violet';
  outcomes: string[];
};

export const TOGGLE_FIELDS: StealthToggleSpec[] = [
  {
    key: 'enabled',
    label: 'Stealth master',
    description: 'Turns all stealth-specific BrowserContext mutations on or off.',
    icon: 'shield',
    accent: 'teal',
    outcomes: ['Context options applied', 'Init scripts injected', 'Header spoofing enabled'],
  },
  {
    key: 'rotate_user_agent',
    label: 'Rotate User-Agent',
    description: 'Chooses a user agent from the configured pool for each new agent session.',
    icon: 'refresh',
    accent: 'amber',
    outcomes: ['Playwright user_agent set', 'Identity entropy between sessions'],
  },
  {
    key: 'mask_webdriver',
    label: 'Mask webdriver',
    description: 'Overrides navigator.webdriver and related fingerprint surfaces in JS.',
    icon: 'sparkles',
    accent: 'violet',
    outcomes: ['navigator.webdriver hidden', 'Plugin/WebGL script mutations active'],
  },
];

export function normalizeLanguagesDraft(draft: string): string[] {
  return draft
    .split(/[\s,]+/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

export function normalizeUserAgentsDraft(draft: string): string[] {
  return draft
    .split(/\r?\n/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

export function sameConfig(left: StealthConfig, right: StealthConfig): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function activeUserAgent(config: StealthConfig): string {
  if (!config.enabled) return 'Browser default fingerprint path';
  if (config.user_agents.length === 0) return 'No custom User-Agent override';
  if (config.rotate_user_agent) return 'Random from pool on each agent rebuild';
  return config.user_agents[0];
}

export function browserContextPreview(
  config: StealthConfig
): Array<{ label: string; value: string }> {
  if (!config.enabled) return [];
  const preview: Array<{ label: string; value: string }> = [];
  if (config.user_agents.length > 0) {
    preview.push({
      label: 'user_agent',
      value: config.rotate_user_agent ? 'randomized from pool' : config.user_agents[0],
    });
  }
  if (config.spoof_languages.length > 0) {
    preview.push({ label: 'locale', value: config.spoof_languages[0] });
  }
  if (config.spoof_timezone.trim()) {
    preview.push({ label: 'timezone_id', value: config.spoof_timezone.trim() });
  }
  return preview;
}

export function headerPreview(config: StealthConfig): Array<{ label: string; value: string }> {
  if (!config.enabled || config.spoof_languages.length === 0) return [];
  return [{ label: 'Accept-Language', value: config.spoof_languages.join(', ') }];
}
