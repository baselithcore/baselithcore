import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ErrorBoundary } from './ErrorBoundary';

function Bomb(): never {
  throw new Error('kaboom');
}

describe('ErrorBoundary', () => {
  it('renders the fallback UI (and not the crashing child) when a child throws', () => {
    // React logs the caught error to the console in dev; keep test output clean.
    vi.spyOn(console, 'error').mockImplementation(() => {});

    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>
    );

    expect(screen.getByText('Frontend error')).toBeInTheDocument();
    expect(screen.getByText('kaboom')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reload dashboard' })).toBeInTheDocument();
  });

  it('renders children normally when nothing throws', () => {
    render(
      <ErrorBoundary>
        <div>all good</div>
      </ErrorBoundary>
    );

    expect(screen.getByText('all good')).toBeInTheDocument();
    expect(screen.queryByText('Frontend error')).not.toBeInTheDocument();
  });
});
