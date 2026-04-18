import { forwardRef } from 'react';
import { Panel } from '../../../components/Panel';
import { Icon, paths } from '../../../lib/icons';

interface TaskFormProps {
  goal: string;
  setGoal: (value: string) => void;
  startUrl: string;
  setStartUrl: (value: string) => void;
  maxSteps: number;
  setMaxSteps: (value: number) => void;
  extract: string;
  setExtract: (value: string) => void;
  disabled: boolean;
  isPending: boolean;
  onSubmit: () => void;
  onReset: () => void;
}

export const TaskForm = forwardRef<HTMLTextAreaElement, TaskFormProps>(function TaskForm(
  {
    goal,
    setGoal,
    startUrl,
    setStartUrl,
    maxSteps,
    setMaxSteps,
    extract,
    setExtract,
    disabled,
    isPending,
    onSubmit,
    onReset,
  },
  ref
) {
  return (
    <Panel title="Task" tag="POST /baselithbot/run">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (disabled) return;
          onSubmit();
        }}
        style={{ display: 'flex', flexDirection: 'column', gap: 14 }}
      >
        <div className="form-row">
          <label htmlFor="goal">Goal</label>
          <textarea
            id="goal"
            ref={ref}
            className="textarea"
            placeholder="e.g. Navigate to github.com and extract the trending repo titles"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            required
            maxLength={4000}
          />
        </div>

        <div className="form-row">
          <label htmlFor="starturl">Start URL (optional)</label>
          <input
            id="starturl"
            className="input"
            type="url"
            placeholder="https://example.com"
            value={startUrl}
            onChange={(e) => setStartUrl(e.target.value)}
          />
        </div>

        <div className="inline">
          <div className="form-row" style={{ flex: '0 0 160px' }}>
            <label htmlFor="steps">Max steps</label>
            <input
              id="steps"
              className="input"
              type="number"
              min={1}
              max={100}
              value={maxSteps}
              onChange={(e) =>
                setMaxSteps(Math.max(1, Math.min(100, Number(e.target.value) || 20)))
              }
            />
          </div>
          <div className="form-row" style={{ flex: 1 }}>
            <label htmlFor="extract">Extract fields (comma separated)</label>
            <input
              id="extract"
              className="input"
              placeholder="title, price, rating"
              value={extract}
              onChange={(e) => setExtract(e.target.value)}
            />
          </div>
        </div>

        <div className="inline" style={{ justifyContent: 'flex-end' }}>
          <button type="button" className="btn ghost" disabled={isPending} onClick={onReset}>
            Reset
          </button>
          <button type="submit" className="btn primary" disabled={disabled}>
            {isPending ? (
              <>
                <span className="spin">
                  <Icon path={paths.refresh} size={14} />
                </span>
                Dispatching…
              </>
            ) : (
              <>
                <Icon path={paths.play} size={14} />
                Launch
              </>
            )}
          </button>
        </div>
      </form>
    </Panel>
  );
});
