import { useState, type FormEvent, type ReactNode } from 'react';
import { Fingerprint, Eye, EyeOff, ArrowRight, ShieldCheck } from 'lucide-react';
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { ApiError } from '../services/apiClient';
import { authService } from '../services/auth';
import { selfRegistrationEnabled } from '../auth/config';
import { useAuth } from '../auth/useAuth';

function AuthFrame({ children, title, detail }: { children: ReactNode; title: string; detail: string }) {
  return <main className="auth-page">
    <Link to="/" className="brand auth-brand"><span className="brand-mark"><Fingerprint size={18}/></span><span>DocForens<span className="lime">IQ</span></span></Link>
    <section className="auth-card">
      <div className="auth-mark"><ShieldCheck size={18}/><span>SECURE WORKSPACE ACCESS</span></div>
      <h1>{title}</h1><p className="auth-detail">{detail}</p>
      {children}
      <div className="auth-security-note">Sessions are protected with secure, HttpOnly cookies.</div>
    </section>
    <div className="auth-foot">DOCFORENSIQ · EVIDENCE-LED DOCUMENT INTELLIGENCE</div>
  </main>;
}

function ErrorMessage({ children }: { children: ReactNode }) {
  return <div className="auth-error" role="alert">{children}</div>;
}

function PasswordField({ label, value, onChange, autoComplete }: { label: string; value: string; onChange: (value: string) => void; autoComplete: string }) {
  const [visible, setVisible] = useState(false);
  return <label className="auth-label">{label}
    <span className="password-input"><input aria-label={label} type={visible ? 'text' : 'password'} autoComplete={autoComplete} value={value} onChange={(event) => onChange(event.target.value)} required/><button type="button" className="password-toggle" onClick={() => setVisible((open) => !open)} aria-label={visible ? 'Hide password' : 'Show password'}>{visible ? <EyeOff size={16}/> : <Eye size={16}/>}</button></span>
  </label>;
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return 'Unable to reach the authentication service. Check your connection and try again.';
}

export function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const destination = (location.state as { from?: string } | null)?.from || '/';
  if (user) return <Navigate to={destination} replace/>;
  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true); setError('');
    try { await login({ email, password }); navigate(destination, { replace: true }); }
    catch (reason) { setError(errorText(reason)); }
    finally { setSubmitting(false); }
  }
  return <AuthFrame title="Welcome back" detail="Sign in to continue to your investigation workspace.">
    <form className="auth-form" onSubmit={submit}>
      <label className="auth-label">Email address<input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required/></label>
      <PasswordField label="Password" value={password} onChange={setPassword} autoComplete="current-password"/>
      {error && <ErrorMessage>{error}</ErrorMessage>}
      <div className="auth-row"><span>Protected investigator access</span><Link to="/forgot-password">Forgot password?</Link></div>
      <button className="button primary auth-submit" disabled={submitting}>{submitting ? 'Signing in…' : 'Sign in'}{!submitting && <ArrowRight size={15}/>}</button>
    </form>
    {selfRegistrationEnabled ? <div className="auth-switch">New to DocForensIQ? <Link to="/register">Create an account</Link></div> : <div className="auth-switch">Need access? Contact your workspace administrator.</div>}
  </AuthFrame>;
}

export function RegisterPage() {
  const { user, register } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  if (!selfRegistrationEnabled) return <AuthFrame title="Account access" detail="Accounts are provisioned by a workspace administrator."><Link className="button primary auth-submit" to="/login">Return to sign in</Link></AuthFrame>;
  if (user) return <Navigate to="/" replace/>;
  async function submit(event: FormEvent) {
    event.preventDefault(); setError('');
    if (password !== confirm) { setError('Passwords do not match.'); return; }
    setSubmitting(true);
    try { await register(name, email, password); navigate('/', { replace: true }); }
    catch (reason) { setError(errorText(reason)); }
    finally { setSubmitting(false); }
  }
  return <AuthFrame title="Create your account" detail="Create a secure investigator account for your workspace.">
    <form className="auth-form" onSubmit={submit}>
      <label className="auth-label">Full name<input autoComplete="name" value={name} onChange={(event) => setName(event.target.value)} required maxLength={160}/></label>
      <label className="auth-label">Email address<input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required/></label>
      <PasswordField label="Password" value={password} onChange={setPassword} autoComplete="new-password"/>
      <div className="password-policy">At least 12 characters with uppercase, lowercase, and a number.</div>
      <PasswordField label="Confirm password" value={confirm} onChange={setConfirm} autoComplete="new-password"/>
      {error && <ErrorMessage>{error}</ErrorMessage>}
      <button className="button primary auth-submit" disabled={submitting}>{submitting ? 'Creating account…' : 'Create account'}{!submitting && <ArrowRight size={15}/>}</button>
    </form>
    <div className="auth-switch">Already registered? <Link to="/login">Sign in</Link></div>
  </AuthFrame>;
}

export function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage(''); setError('');
    try { setMessage((await authService.requestReset(email)).message); }
    catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }
  return <AuthFrame title="Reset your password" detail="Enter your account email and we’ll send instructions if an active account matches.">
    <form className="auth-form" onSubmit={submit}>
      <label className="auth-label">Email address<input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required/></label>
      {error && <ErrorMessage>{error}</ErrorMessage>}
      {message && <div className="auth-success" role="status">{message}</div>}
      <button className="button primary auth-submit" disabled={busy}>{busy ? 'Sending…' : 'Send reset instructions'}{!busy && <ArrowRight size={15}/>}</button>
    </form><div className="auth-switch"><Link to="/login">Back to sign in</Link></div>
  </AuthFrame>;
}

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setError('');
    if (!params.get('token')) { setError('This reset link is incomplete. Request a new one.'); return; }
    if (password !== confirm) { setError('Passwords do not match.'); return; }
    setBusy(true);
    try { await authService.confirmReset(params.get('token')!, password); navigate('/login', { replace: true }); }
    catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }
  return <AuthFrame title="Choose a new password" detail="Use a strong password that meets the workspace policy.">
    <form className="auth-form" onSubmit={submit}>
      <PasswordField label="New password" value={password} onChange={setPassword} autoComplete="new-password"/>
      <div className="password-policy">At least 12 characters with uppercase, lowercase, and a number.</div>
      <PasswordField label="Confirm new password" value={confirm} onChange={setConfirm} autoComplete="new-password"/>
      {error && <ErrorMessage>{error}</ErrorMessage>}
      <button className="button primary auth-submit" disabled={busy}>{busy ? 'Updating…' : 'Update password'}{!busy && <ArrowRight size={15}/>}</button>
    </form>
  </AuthFrame>;
}
