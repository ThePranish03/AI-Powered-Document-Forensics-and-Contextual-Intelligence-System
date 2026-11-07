import { apiRequest } from './apiClient';

export interface AuthUser {
  id: string;
  full_name: string;
  email: string;
  role: 'admin' | 'investigator' | 'reviewer';
  permissions: string[];
}

export interface Credentials {
  email: string;
  password: string;
}

export const authService = {
  me: () => apiRequest<AuthUser>('/auth/me'),
  login: (credentials: Credentials) => apiRequest<AuthUser>('/auth/login', { method: 'POST', body: JSON.stringify(credentials) }),
  register: (full_name: string, email: string, password: string) => apiRequest<AuthUser>('/auth/register', { method: 'POST', body: JSON.stringify({ full_name, email, password }) }),
  logout: () => apiRequest<void>('/auth/logout', { method: 'POST' }),
  requestReset: (email: string) => apiRequest<{ message: string }>('/auth/password-reset/request', { method: 'POST', body: JSON.stringify({ email }) }),
  confirmReset: (token: string, password: string) => apiRequest<{ message: string }>('/auth/password-reset/confirm', { method: 'POST', body: JSON.stringify({ token, password }) }),
};
