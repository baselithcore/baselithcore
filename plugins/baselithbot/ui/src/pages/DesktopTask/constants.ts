import type { IconName } from '../../lib/icons';

export type CapabilityKey =
  | 'allow_screenshot'
  | 'allow_mouse'
  | 'allow_keyboard'
  | 'allow_shell'
  | 'allow_filesystem';

export interface CapabilitySpec {
  key: CapabilityKey;
  approvalKey: string;
  label: string;
  description: string;
  icon: IconName;
  accent: 'teal' | 'cyan' | 'violet' | 'amber' | 'rose';
  toolNames: string[];
}

export const CAPABILITIES: CapabilitySpec[] = [
  {
    key: 'allow_screenshot',
    approvalKey: 'screenshot',
    label: 'Screen',
    description: 'Capture frames and probe host geometry.',
    icon: 'copy',
    accent: 'teal',
    toolNames: ['baselithbot_desktop_screenshot', 'baselithbot_screen_size'],
  },
  {
    key: 'allow_mouse',
    approvalKey: 'mouse',
    label: 'Pointer',
    description: 'Absolute move, click, and wheel dispatch.',
    icon: 'activity',
    accent: 'cyan',
    toolNames: ['baselithbot_mouse_move', 'baselithbot_mouse_click', 'baselithbot_mouse_scroll'],
  },
  {
    key: 'allow_keyboard',
    approvalKey: 'keyboard',
    label: 'Keyboard',
    description: 'Type text, press keys, and send hotkeys.',
    icon: 'terminal',
    accent: 'violet',
    toolNames: ['baselithbot_kbd_type', 'baselithbot_kbd_press', 'baselithbot_kbd_hotkey'],
  },
  {
    key: 'allow_shell',
    approvalKey: 'shell',
    label: 'Shell',
    description: 'Allowlisted subprocess execution on the operator host.',
    icon: 'zap',
    accent: 'amber',
    toolNames: ['baselithbot_shell_run'],
  },
  {
    key: 'allow_filesystem',
    approvalKey: 'filesystem',
    label: 'Filesystem',
    description: 'Scoped read, write, and directory enumeration.',
    icon: 'box',
    accent: 'rose',
    toolNames: ['baselithbot_fs_read', 'baselithbot_fs_write', 'baselithbot_fs_list'],
  },
];

export const EXPECTED_TOOL_NAMES = CAPABILITIES.flatMap((capability) => capability.toolNames);
