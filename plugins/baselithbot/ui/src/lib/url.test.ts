import { describe, expect, it } from 'vitest';
import { safeExternalUrl } from './url';

describe('safeExternalUrl', () => {
  it('accepts absolute http and https URLs', () => {
    expect(safeExternalUrl('https://example.com/a?b=1')).toBe('https://example.com/a?b=1');
    expect(safeExternalUrl('  http://example.com ')).toBe('http://example.com/');
  });

  it.each([
    'javascript:alert(1)',
    ' JaVaScRiPt:alert(1)',
    'java\tscript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:msgbox(1)',
    '/relative/path',
    '//evil.example',
    '',
  ])('rejects %j', (raw) => {
    expect(safeExternalUrl(raw)).toBeNull();
  });

  it('rejects non-strings', () => {
    expect(safeExternalUrl(undefined)).toBeNull();
    expect(safeExternalUrl(42)).toBeNull();
  });
});
