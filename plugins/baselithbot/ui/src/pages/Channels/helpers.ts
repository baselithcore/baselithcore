import type { Channel } from '../../lib/api';

export type StatusFilter = 'all' | 'live' | 'configured' | 'missing';
export type SortKey = 'events' | 'name' | 'status';

export function statusLabel(channel: Channel): { label: string; tone: 'ok' | 'muted' | 'warn' } {
  if (channel.live) return { label: 'live', tone: 'ok' };
  if (channel.enabled && !channel.configured) return { label: 'needs config', tone: 'warn' };
  if (channel.configured) return { label: 'configured', tone: 'muted' };
  return { label: 'registered', tone: 'muted' };
}

export function statusAccent(channel: Channel): 'live' | 'configured' | 'missing' {
  if (channel.live) return 'live';
  if (channel.configured) return 'configured';
  return 'missing';
}

export function isSensitiveField(name: string): boolean {
  const lower = name.toLowerCase();
  return (
    lower.endsWith('token') ||
    lower.endsWith('key') ||
    lower.endsWith('password') ||
    lower.endsWith('secret') ||
    lower === 'private_key_hex'
  );
}

export const FIELD_PLACEHOLDERS: Record<string, string> = {
  webhook_url: 'https://hooks.slack.com/services/… or provider webhook URL',
  gateway_url: 'https://gateway.example.com/hooks/messages',
  server_url: 'https://server.example.com',
  rpc_url: 'http://127.0.0.1:8080/api/v1/rpc',
  relay_url: 'wss://relay.example.com',
  homeserver: 'https://matrix.example.org',
  server: 'irc.libera.chat',
  username: 'bot-user or service account',
  password: 'channel password or app password',
  nick: 'baselithbot',
  oauth_token: 'oauth:xxxxxxxxxxxxxxxx',
  bot_token: '1234567890:AAExampleTelegramBotToken',
  channel_access_token: 'LINE channel access token',
  access_token: 'provider access token',
  private_key_hex: '64-character hex private key',
  public_key_hex: '64-character hex public key',
  from_number: '+393331234567',
  phone_number_id: 'WhatsApp phone number id',
  room_id: '!ops:example.org',
  default_channel: '#ops-alerts',
  api_version: 'v19.0',
};

export function titleCaseField(name: string): string {
  return name.replace(/_/g, ' ');
}

export function placeholderForField(
  channelName: string,
  fieldName: string,
  currentValue: string | number | boolean | undefined
): string {
  const current = currentValue === undefined ? '' : String(currentValue);
  const lower = fieldName.toLowerCase();

  const channelSpecific: Record<string, Record<string, string>> = {
    slack: { webhook_url: 'https://hooks.slack.com/services/T…/B…/…' },
    discord: { webhook_url: 'https://discord.com/api/webhooks/.../...' },
    microsoft_teams: { webhook_url: 'https://outlook.office.com/webhook/…' },
    google_chat: { webhook_url: 'https://chat.googleapis.com/v1/spaces/.../messages?key=…' },
    telegram: { bot_token: '1234567890:AAExampleTelegramBotToken' },
    whatsapp: {
      access_token: 'Meta WhatsApp Cloud API access token',
      phone_number_id: '123456789012345',
    },
    matrix: {
      homeserver: 'https://matrix.example.org',
      access_token: 'Matrix access token',
      room_id: '!ops:example.org',
    },
    signal: {
      rpc_url: 'http://127.0.0.1:8080/api/v1/rpc',
      from_number: '+393331234567',
    },
    irc: {
      server: 'irc.libera.chat',
      nick: 'baselithbot',
    },
    nostr: {
      relay_url: 'wss://relay.example.com',
      private_key_hex: '64-character hex private key',
      public_key_hex: '64-character hex public key',
    },
    nextcloud_talk: {
      server_url: 'https://cloud.example.com',
      username: 'bot-user',
      password: 'app password',
    },
    bluebubbles: {
      server_url: 'https://bluebubbles.example.com',
      password: 'BlueBubbles password',
    },
    twitch: {
      oauth_token: 'oauth:xxxxxxxxxxxxxxxx',
      nick: 'your_twitch_bot',
    },
  };

  const specific = channelSpecific[channelName]?.[fieldName];
  const generic = FIELD_PLACEHOLDERS[fieldName];
  const base =
    specific ??
    generic ??
    (lower.endsWith('_url')
      ? 'https://example.com/...'
      : lower.endsWith('_token') || lower.endsWith('_key')
        ? `Paste ${titleCaseField(fieldName)}`
        : lower.includes('number')
          ? '+393331234567'
          : `Enter ${titleCaseField(fieldName)}`);

  if (current) {
    return isSensitiveField(fieldName)
      ? `saved: ${current} · paste a new value only to replace`
      : `current: ${current}`;
  }
  return base;
}
