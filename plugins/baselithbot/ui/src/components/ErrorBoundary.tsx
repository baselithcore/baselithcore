import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  message: string;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = {
    hasError: false,
    message: '',
  };

  static getDerivedStateFromError(error: Error): State {
    return {
      hasError: true,
      message: error.message || 'Unexpected frontend error.',
    };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('baselithbot_ui_error_boundary', {
      message: error.message,
      stack: error.stack,
      componentStack: info.componentStack,
    });
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-shell">
          <div className="error-card">
            <div className="eyebrow">Baselithbot UI</div>
            <h1>Frontend error</h1>
            <p>{this.state.message}</p>
            <div className="inline">
              <button
                type="button"
                className="btn primary"
                onClick={() => window.location.reload()}
              >
                Reload dashboard
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
