import { useState } from 'react';
import { Panel } from '../../../components/Panel';
import type { DesktopShared } from '../shared';

export function KeyboardSection({ shared }: { shared: DesktopShared }) {
  const { policy, canUse, invoke } = shared;
  const [typeText, setTypeText] = useState('');
  const [pressKey, setPressKey] = useState('');
  const [hotkeyStr, setHotkeyStr] = useState('cmd,space');

  return (
    <Panel title="Keyboard" tag={policy.allow_keyboard ? 'enabled' : 'gated'}>
      <div className="desktop-action-card">
        <div className="desktop-action-head">
          <div>
            <strong>Type, press, and hotkey</strong>
            <p className="muted">
              Keyboard actions are wired to the exported tool catalog and respect the same
              approval flow as pointer actions.
            </p>
          </div>
          <span className={`badge ${policy.allow_keyboard ? 'ok' : 'muted'}`}>
            keyboard {policy.allow_keyboard ? 'enabled' : 'disabled'}
          </span>
        </div>

        <div className="form-row">
          <label htmlFor="ktype">Type text</label>
          <div className="inline">
            <input
              id="ktype"
              className="input"
              placeholder="hello world"
              value={typeText}
              onChange={(e) => setTypeText(e.target.value)}
              style={{ flex: 1 }}
            />
            <button
              type="button"
              className="btn primary"
              disabled={
                !canUse('baselithbot_kbd_type', 'allow_keyboard') || typeText.length === 0
              }
              onClick={() => invoke('baselithbot_kbd_type', { text: typeText, interval: 0.0 })}
            >
              Type
            </button>
          </div>
        </div>

        <div className="desktop-form-grid">
          <div className="form-row">
            <label htmlFor="kpress">Press single key</label>
            <div className="inline">
              <input
                id="kpress"
                className="input"
                placeholder="enter"
                value={pressKey}
                onChange={(e) => setPressKey(e.target.value)}
                style={{ flex: 1 }}
              />
              <button
                type="button"
                className="btn"
                disabled={
                  !canUse('baselithbot_kbd_press', 'allow_keyboard') ||
                  pressKey.trim().length === 0
                }
                onClick={() => invoke('baselithbot_kbd_press', { key: pressKey.trim() })}
              >
                Press
              </button>
            </div>
          </div>

          <div className="form-row">
            <label htmlFor="khot">Hotkey chord</label>
            <div className="inline">
              <input
                id="khot"
                className="input mono"
                placeholder="cmd,space"
                value={hotkeyStr}
                onChange={(e) => setHotkeyStr(e.target.value)}
                style={{ flex: 1 }}
              />
              <button
                type="button"
                className="btn"
                disabled={
                  !canUse('baselithbot_kbd_hotkey', 'allow_keyboard') ||
                  hotkeyStr.trim().length === 0
                }
                onClick={() => {
                  const keys = hotkeyStr
                    .split(',')
                    .map((key) => key.trim())
                    .filter(Boolean);
                  if (keys.length === 0) return;
                  invoke('baselithbot_kbd_hotkey', { keys });
                }}
              >
                Hotkey
              </button>
            </div>
          </div>
        </div>
      </div>
    </Panel>
  );
}
