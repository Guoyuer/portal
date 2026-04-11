"use client";

import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="max-w-5xl mx-auto py-20 text-center">
          <p className="text-red-500 mb-2">Something went wrong</p>
          <p className="text-sm text-muted-foreground mb-4">{this.state.error.message}</p>
          <button
            onClick={() => this.setState({ error: null })}
            className="px-4 py-2 text-sm rounded-md border border-border hover:bg-foreground/5 transition-colors"
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

/** Inline fallback for section-level error boundaries */
export function SectionError({ label }: { label: string }) {
  return (
    <div className="liquid-glass p-4 text-center text-sm text-red-400">
      {label} failed to render
    </div>
  );
}
