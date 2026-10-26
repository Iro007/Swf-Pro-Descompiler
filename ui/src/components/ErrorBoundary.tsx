import {Component,ErrorInfo,ReactNode} from 'react';

interface Props {
  children: ReactNode;
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface State {
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('ErrorBoundary caught:', error, info);
  }

  reset = () => {
    this.setState({ error: null });
  };

  render() {
    if (this.state.error) {
      if (this.props.fallback) {
        return this.props.fallback(this.state.error, this.reset);
      }
      return (
        <div className="panel-error" role="alert" style={{ padding: '16px', margin: '16px' }}>
          <h4>Ocurrió un error al cargar la vista</h4>
          <p>{this.state.error.message}</p>
          <button type="button" className="btn-secondary" onClick={this.reset} style={{ marginTop: '8px' }}>
            Reintentar
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
