import { createContext } from 'react';
import type { AuthUser, Credentials } from '../services/auth';

export interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  login: (credentials: Credentials) => Promise<AuthUser>;
  register: (name: string, email: string, password: string) => Promise<AuthUser>;
  logout: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
