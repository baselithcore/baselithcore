import { useState } from 'react';
import { Panel } from '../../../components/Panel';
import { Icon, paths } from '../../../lib/icons';
import type { DesktopShared } from '../shared';

export function ScreenPointerSection({ shared }: { shared: DesktopShared }) {
  const { policy, toolMap, canUse, invoke } = shared;
  const [screenshotMonitor, setScreenshotMonitor] = useState(1);
  const [screenshotFormat, setScreenshotFormat] = useState<'PNG' | 'JPEG' | 'WEBP'>('PNG');
  const [screenshotQuality, setScreenshotQuality] = useState(80);
  const [mouseX, setMouseX] = useState(0);
  const [mouseY, setMouseY] = useState(0);
  const [mouseButton, setMouseButton] = useState<'left' | 'right' | 'middle'>('left');
  const [mouseClicks, setMouseClicks] = useState(1);
  const [scrollAmount, setScrollAmount] = useState(-400);

  return (
    <Panel title="Screen and pointer" tag={policy.allow_screenshot ? 'enabled' : 'gated'}>
      <div className="desktop-action-card">
        <div className="desktop-action-head">
          <div>
            <strong>Capture and geometry probe</strong>
            <p className="muted">
              Use the live screenshot tool exported by the plugin. Format and quality are
              forwarded unchanged to the backend handler.
            </p>
          </div>
          <div className="chip-row">
            <span
              className={`badge ${toolMap.has('baselithbot_desktop_screenshot') ? 'ok' : 'err'}`}
            >
              screenshot
            </span>
            <span className={`badge ${toolMap.has('baselithbot_screen_size') ? 'ok' : 'err'}`}>
              screen size
            </span>
          </div>
        </div>

        <div className="desktop-form-grid">
          <div className="form-row">
            <label htmlFor="monitor">Monitor</label>
            <input
              id="monitor"
              className="input"
              type="number"
              min={1}
              value={screenshotMonitor}
              onChange={(e) => setScreenshotMonitor(Math.max(1, Number(e.target.value) || 1))}
            />
          </div>
          <div className="form-row">
            <label htmlFor="format">Format</label>
            <select
              id="format"
              className="select"
              value={screenshotFormat}
              onChange={(e) => setScreenshotFormat(e.target.value as 'PNG' | 'JPEG' | 'WEBP')}
            >
              <option value="PNG">PNG</option>
              <option value="JPEG">JPEG</option>
              <option value="WEBP">WEBP</option>
            </select>
          </div>
          <div className="form-row">
            <label htmlFor="quality">Quality</label>
            <input
              id="quality"
              className="input"
              type="number"
              min={1}
              max={100}
              value={screenshotQuality}
              onChange={(e) =>
                setScreenshotQuality(Math.min(100, Math.max(1, Number(e.target.value) || 80)))
              }
            />
          </div>
        </div>

        <div className="inline" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <span className="muted mono" style={{ fontSize: 12 }}>
            {policy.require_approval_for.includes('screenshot')
              ? `approval timeout ${policy.approval_timeout_seconds}s`
              : 'direct execution'}
          </span>
          <div className="inline">
            <button
              type="button"
              className="btn ghost"
              disabled={!canUse('baselithbot_screen_size', 'allow_screenshot')}
              onClick={() => invoke('baselithbot_screen_size', {})}
            >
              Screen size
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={!canUse('baselithbot_desktop_screenshot', 'allow_screenshot')}
              onClick={() =>
                invoke('baselithbot_desktop_screenshot', {
                  monitor: screenshotMonitor,
                  image_format: screenshotFormat,
                  quality: screenshotQuality,
                })
              }
            >
              <Icon path={paths.copy} size={14} />
              Capture
            </button>
          </div>
        </div>
      </div>

      <div className="desktop-action-card">
        <div className="desktop-action-head">
          <div>
            <strong>Pointer controls</strong>
            <p className="muted">
              Mouse move, click, and scroll are dispatched as direct tool calls and respect
              the same approval gate as the agent runtime.
            </p>
          </div>
          <span className={`badge ${policy.allow_mouse ? 'ok' : 'muted'}`}>
            mouse {policy.allow_mouse ? 'enabled' : 'disabled'}
          </span>
        </div>

        <div className="desktop-form-grid desktop-form-grid-wide">
          <div className="form-row">
            <label htmlFor="mx">X</label>
            <input
              id="mx"
              className="input"
              type="number"
              value={mouseX}
              onChange={(e) => setMouseX(Number(e.target.value) || 0)}
            />
          </div>
          <div className="form-row">
            <label htmlFor="my">Y</label>
            <input
              id="my"
              className="input"
              type="number"
              value={mouseY}
              onChange={(e) => setMouseY(Number(e.target.value) || 0)}
            />
          </div>
          <div className="form-row">
            <label htmlFor="mb">Button</label>
            <select
              id="mb"
              className="select"
              value={mouseButton}
              onChange={(e) => setMouseButton(e.target.value as 'left' | 'right' | 'middle')}
            >
              <option value="left">left</option>
              <option value="right">right</option>
              <option value="middle">middle</option>
            </select>
          </div>
          <div className="form-row">
            <label htmlFor="mc">Clicks</label>
            <input
              id="mc"
              className="input"
              type="number"
              min={1}
              max={5}
              value={mouseClicks}
              onChange={(e) => setMouseClicks(Math.max(1, Number(e.target.value) || 1))}
            />
          </div>
          <div className="form-row">
            <label htmlFor="scroll">Scroll amount</label>
            <input
              id="scroll"
              className="input"
              type="number"
              value={scrollAmount}
              onChange={(e) => setScrollAmount(Number(e.target.value) || 0)}
            />
          </div>
        </div>

        <div className="inline">
          <button
            type="button"
            className="btn ghost"
            disabled={!canUse('baselithbot_mouse_move', 'allow_mouse')}
            onClick={() =>
              invoke('baselithbot_mouse_move', { x: mouseX, y: mouseY, duration: 0.0 })
            }
          >
            Move
          </button>
          <button
            type="button"
            className="btn primary"
            disabled={!canUse('baselithbot_mouse_click', 'allow_mouse')}
            onClick={() =>
              invoke('baselithbot_mouse_click', {
                x: mouseX,
                y: mouseY,
                button: mouseButton,
                clicks: mouseClicks,
              })
            }
          >
            Click
          </button>
          <button
            type="button"
            className="btn"
            disabled={!canUse('baselithbot_mouse_scroll', 'allow_mouse')}
            onClick={() => invoke('baselithbot_mouse_scroll', { amount: scrollAmount })}
          >
            Scroll
          </button>
        </div>
      </div>
    </Panel>
  );
}
