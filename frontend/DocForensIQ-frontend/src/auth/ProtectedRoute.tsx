import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from './useAuth';

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, loading, error, refresh } = useAuth();
  const location = useLocation();

  if (loading) return <div className="auth-page"><div className="auth-card" role="status">Restoring your secure session…</div></div>;
  if (error) return <div className="auth-page"><div className="auth-card"><h1>Connection unavailable</h1><p>{error}</p><button className="button primary" onClick={() => void refresh()}>Retry</button></div></div>;
  if (!user) return <Navigate to="/login" replace state={{ from: `${location.pathname}${location.search}` }} />;
  return children;
}
