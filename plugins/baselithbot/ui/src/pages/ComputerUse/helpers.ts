import type { ComputerUseConfig } from '../../lib/api';
import { type IconName } from '../../lib/icons';

export type CapabilityKey =
  | 'allow_screenshot'
  | 'allow_mouse'
  | 'allow_keyboard'
  | 'allow_shell'
  | 'allow_filesystem';

export type CapabilitySpec = {
  key: CapabilityKey;
  label: string;
  description: string;
  icon: IconName;
  accent: 'teal' | 'cyan' | 'violet' | 'amber' | 'rose';
  danger?: boolean;
  tools: string[];
};

export const CAPABILITY_FIELDS: CapabilitySpec[] = [
  {
    key: 'allow_screenshot',
    label: 'Screenshots',
    description: 'Desktop capture and geometry probes for screen-state awareness.',
    icon: 'copy',
    accent: 'teal',
    tools: ['baselithbot_desktop_screenshot', 'baselithbot_screen_size'],
  },
  {
    key: 'allow_mouse',
    label: 'Mouse control',
    description: 'Absolute move, click, and wheel events on the operator machine.',
    icon: 'activity',
    accent: 'cyan',
    tools: ['baselithbot_mouse_move', 'baselithbot_mouse_click', 'baselithbot_mouse_scroll'],
  },
  {
    key: 'allow_keyboard',
    label: 'Keyboard control',
    description: 'Typing, single-key dispatch, and hotkey chords.',
    icon: 'terminal',
    accent: 'violet',
    tools: ['baselithbot_kbd_type', 'baselithbot_kbd_press', 'baselithbot_kbd_hotkey'],
  },
  {
    key: 'allow_shell',
    label: 'Shell execution',
    description: 'Allowlisted subprocess calls and process management.',
    icon: 'zap',
    accent: 'amber',
    danger: true,
    tools: ['baselithbot_shell_run', 'baselithbot_process_list', 'baselithbot_process_kill'],
  },
  {
    key: 'allow_filesystem',
    label: 'Filesystem scope',
    description: 'Read, write, patch, and enumerate content under a single root.',
    icon: 'box',
    accent: 'rose',
    danger: true,
    tools: [
      'baselithbot_fs_read',
      'baselithbot_fs_write',
      'baselithbot_fs_list',
      'baselithbot_code_diff_apply',
      'baselithbot_code_line_edit',
      'baselithbot_code_search_replace',
      'baselithbot_code_multi_file_write',
    ],
  },
];

export function normalizeAllowlistDraft(draft: string): string[] {
  return draft
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

export function sameConfig(left: ComputerUseConfig, right: ComputerUseConfig): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function summariseAllowlist(entries: string[]): string {
  if (entries.length === 0) return 'No commands allowlisted';
  if (entries.length === 1) return entries[0];
  if (entries.length === 2) return `${entries[0]} and ${entries[1]}`;
  return `${entries[0]}, ${entries[1]}, +${entries.length - 2} more`;
}

export function capabilityTone(
  accent: CapabilitySpec['accent']
): 'ok' | 'warn' | 'err' | 'muted' {
  if (accent === 'teal' || accent === 'cyan' || accent === 'violet') return 'ok';
  if (accent === 'amber') return 'warn';
  return 'err';
}
