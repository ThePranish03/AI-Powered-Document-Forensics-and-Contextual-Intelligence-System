import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../services/apiClient';
import { authService, type AuthUser, type Credentials } from '../services/auth';
import { AuthContext } from './authContext';

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setUser(await authService.me());
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) {
        setUser(null);
      } else {
        setError(reason instanceof Error ? reason.message : 'Unable to restore your session.');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const expireSession = () => {
      queryClient.clear();
      setUser(null);
    };
    window.addEventListener('docforensiq:unauthorized', expireSession);
    return () => window.removeEventListener('docforensiq:unauthorized', expireSession);
  }, [queryClient, refresh]);

  const login = useCallback(async (credentials: Credentials) => {
    const signedIn = await authService.login(credentials);
    queryClient.clear();
    setUser(signedIn);
    setError(null);
    return signedIn;
  }, [queryClient]);

  const register = useCallback(async (name: string, email: string, password: string) => {
    const registered = await authService.register(name, email, password);
    queryClient.clear();
    setUser(registered);
    setError(null);
    return registered;
  }, [queryClient]);

  const logout = useCallback(async () => {
    await authService.logout();
    queryClient.clear();
    setUser(null);
  }, [queryClient]);

  const value = useMemo(() => ({ user, loading, error, refresh, login, register, logout }), [user, loading, error, refresh, login, register, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
