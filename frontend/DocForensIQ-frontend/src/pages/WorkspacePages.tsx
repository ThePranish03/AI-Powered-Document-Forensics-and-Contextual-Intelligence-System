import { useEffect, useMemo, useState, type DragEvent, type FormEvent, type ReactNode } from 'react';
import { Activity, ArrowRight, Bell, BookOpen, BriefcaseBusiness, CheckCircle2, ChevronDown, ChevronLeft, Clock3, Eye, FileCheck2, FileImage, FileSearch, FileText, Fingerprint, GitBranch, Home, Layers3, LogOut, Menu, Network, Plus, Search, ShieldCheck, SlidersHorizontal, type LucideIcon } from 'lucide-react';
import { Link, NavLink, Outlet, useNavigate, useParams, useLocation, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../auth/useAuth';
import { mockApiEnabled } from '../auth/config';
import { apiBlob, apiRequest, ApiError } from '../services/apiClient';
import RelationshipExplorer, { type RelationshipClaim, type RelationshipDocument, type RelationshipEntity } from '../features/relationships/RelationshipExplorer';
import EvidenceGraphView from '../features/relationships/EvidenceGraphView';
import type { ApiGraphEdge, ApiGraphNode } from '../features/relationships/graphAdapter';
import InvestigationAnalysis, { type AnalysisSection } from '../features/investigation/InvestigationAnalysis';

interface Summary { total_investigations: number; active_investigations: number; in_review_investigations: number; completed_investigations: number }
interface Investigation { id: string; case_number: string; title: string; status: string; document_count: number; updated_at: string }
interface ActivityItem { id: string; event_type: string; summary: string; created_at: string; case_number: string | null }
const dashboardKeys = { summary: ['dashboard', 'summary'], recent: ['dashboard', 'recent'], activity: ['dashboard', 'activity'] };
const fmtDate = (value: string) => new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(value));

export function WorkspaceLayout({ children }: { children?: ReactNode }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [accountOpen, setAccountOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState('');
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [searchText, setSearchText] = useState('');
  const selector = useQuery({
    queryKey: ['workspace', 'investigation-selector'],
    queryFn: () => apiRequest<Investigation[]>('/dashboard/recent-investigations?limit=20'),
    enabled: Boolean(user),
  });
  const selectorItems = Array.isArray(selector.data) ? selector.data : [];
  const pathMatch = location.pathname.match(/^\/investigations\/([^/]+)/);
  const activeInvestigationId = pathMatch?.[1] && pathMatch[1] !== 'new' ? pathMatch[1] : '';
  const investigationPath = activeInvestigationId ? `/investigations/${activeInvestigationId}` : '';
  const activeInvestigation = selectorItems.find((item) => item.id === activeInvestigationId);
  useEffect(() => {
    setSearchText(new URLSearchParams(location.search).get('search') ?? '');
  }, [location.search]);
  async function signOut() {
    setLoggingOut(true);
    setLogoutError('');
    try { await logout(); navigate('/login', { replace: true }); }
    catch { setLogoutError('Sign out could not be completed. Retry to revoke this session.'); setLoggingOut(false); }
  }
  const initials = user?.full_name.split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase() || 'DF';
  const currentPage = location.pathname.split('/').filter(Boolean).at(-1) || 'workspace';
  const pageLabels: Record<string, string> = {
    new: 'New investigation',
    processing: 'Processing',
    classification: 'Classification',
    structure: 'Structure & visual forensics',
    visual: 'Structure & visual forensics',
    'extracted-data': 'Extracted data',
    evidence: 'Evidence viewer',
    compare: 'Cross-document comparison',
    correlation: 'Cross-document comparison',
    relationships: 'Relationship map',
    findings: 'Findings',
    timeline: 'Timeline',
    dossier: 'Dossier',
    audit: 'Audit trail',
    investigations: 'Investigations',
    workspace: 'Overview',
  };
  const pageTitle = activeInvestigationId && currentPage === activeInvestigationId
    ? 'Overview' : pageLabels[currentPage] ?? currentPage.replaceAll('-', ' ');
  const currentCaseTitle = activeInvestigation?.title || activeInvestigation?.case_number || activeInvestigationId;
  const workspaceLinks = [
    { label: 'Home', to: '/', icon: Home, end: true },
    { label: 'Investigations', to: '/investigations', icon: BriefcaseBusiness, end: true },
    { label: 'New investigation', to: '/investigations/new', icon: Plus, end: true },
    { label: 'Reports', to: investigationPath ? `${investigationPath}/dossier` : '/investigations', icon: FileText, end: true },
  ];
  const investigationLinks = investigationPath ? [
    { label: 'Overview', to: investigationPath, icon: Layers3, end: true },
    { label: 'Processing', to: `${investigationPath}/processing`, icon: Activity },
    { label: 'Classification', to: `${investigationPath}/classification`, icon: FileCheck2 },
    { label: 'Structure & Visual Forensics', to: `${investigationPath}/visual`, icon: FileImage },
    { label: 'Extracted Data', to: `${investigationPath}/extracted-data`, icon: FileText },
    { label: 'Evidence Viewer', to: `${investigationPath}/evidence`, icon: Eye },
    { label: 'Cross-Document Comparison', to: `${investigationPath}/correlation`, icon: SlidersHorizontal },
    { label: 'Relationship Map', to: `${investigationPath}/relationships`, icon: Network },
    { label: 'Findings', to: `${investigationPath}/findings`, icon: FileSearch },
    { label: 'Timeline', to: `${investigationPath}/timeline`, icon: Clock3 },
    { label: 'Dossier', to: `${investigationPath}/dossier`, icon: BookOpen },
  ] : [];
  const governanceLinks = investigationPath
    ? [{ label: 'Audit Trail', to: `${investigationPath}/audit`, icon: Fingerprint }]
    : [];
  const legacyCaseRoute = mockApiEnabled && Boolean(activeInvestigationId);
  if (legacyCaseRoute) return <>{children ?? <Outlet/>}</>;

  return <div className="shell">
    <aside className={`sidebar workspace-sidebar ${collapsed ? 'collapsed' : ''} ${mobileOpen ? 'mobile-open' : ''}`}>
      <Link to="/" className="brand"><span className="brand-mark"><Fingerprint size={18}/></span><span>DocForens<span className="lime">IQ</span></span></Link>
      <label className="workspace-selector">
        <span>Active investigation</span>
        <span className="workspace-selector-control"><BriefcaseBusiness size={15}/><select aria-label="Active investigation" value={activeInvestigationId} onChange={(event) => {
          if (event.target.value) navigate(`/investigations/${event.target.value}`);
        }}>
          <option value="">{selector.isLoading ? 'Loading investigations…' : 'Select an investigation'}</option>
          {activeInvestigationId && !selectorItems.some((item) => item.id === activeInvestigationId) && <option value={activeInvestigationId}>{currentCaseTitle}</option>}
          {selectorItems.map((item) => <option value={item.id} key={item.id}>{item.case_number} · {item.title}</option>)}
        </select><ChevronDown size={13}/></span>
      </label>
      {selector.isError && !collapsed && <p className="workspace-selector-error" role="status">Investigation list unavailable.</p>}
      <nav className="workspace-nav" aria-label="Workspace navigation">
        <div className="nav-group">
          {!collapsed && <div className="nav-label">WORKSPACE</div>}
          {workspaceLinks.map(({ label, to, icon: Icon, end }) => <NavLink key={label} end={end} to={to} title={collapsed ? label : undefined} aria-label={collapsed ? label : undefined} onClick={() => setMobileOpen(false)} className={({ isActive }) => `nav-link ${isActive && label !== 'Reports' ? 'active' : ''}`}><Icon size={16}/><span>{label}</span></NavLink>)}
        </div>
        {investigationLinks.length > 0 && <div className="nav-group">
          {!collapsed && <div className="nav-label">INVESTIGATION</div>}
          {investigationLinks.map(({ label, to, icon: Icon, end }) => <NavLink key={label} end={end} to={to} title={collapsed ? label : undefined} aria-label={collapsed ? label : undefined} onClick={() => setMobileOpen(false)} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}><Icon size={16}/><span>{label}</span></NavLink>)}
        </div>}
        {governanceLinks.length > 0 && <div className="nav-group">
          {!collapsed && <div className="nav-label">GOVERNANCE</div>}
          {governanceLinks.map(({ label, to, icon: Icon }) => <NavLink key={label} to={to} title={collapsed ? label : undefined} aria-label={collapsed ? label : undefined} onClick={() => setMobileOpen(false)} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}><Icon size={16}/><span>{label}</span></NavLink>)}
        </div>}
      </nav>
      <div className="side-bottom">
        {!collapsed && <div className="demo-pill"><span className="pulse-dot"/>SECURE SESSION</div>}
        <button className="collapse-btn" type="button" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}><ChevronLeft size={16}/>{!collapsed && 'Collapse navigation'}</button>
      </div>
    </aside>
    {mobileOpen && <button className="scrim" onClick={() => setMobileOpen(false)} aria-label="Close navigation"/>}
    <div className="main-shell">
      <header className="topbar"><button className="icon-button mobile-menu" onClick={() => setMobileOpen(true)} aria-label="Open navigation"><Menu size={18}/></button><nav className="crumb" aria-label="Breadcrumb"><Link to="/">Workspace</Link><span aria-hidden="true">/</span>{activeInvestigationId && <><Link to={investigationPath}>{currentCaseTitle}</Link><span aria-hidden="true">/</span></>}<b>{pageTitle}</b></nav><div className="top-actions">
        <form className="global-search" role="search" onSubmit={(event) => { event.preventDefault(); navigate(searchText.trim() ? `/investigations?search=${encodeURIComponent(searchText.trim())}` : '/investigations'); }}>
          <Search size={15}/><input aria-label="Search investigations" value={searchText} onChange={(event) => setSearchText(event.target.value)} placeholder="Search investigations…"/><kbd>Enter</kbd>
        </form>
        <Link className="icon-button" to={investigationPath ? `${investigationPath}/audit` : '/'} aria-label={activeInvestigationId ? 'Open investigation audit trail' : 'Open workspace dashboard activity'}><Bell size={16}/></Link>
        <div className="account-wrap"><button className="account-trigger" aria-label={`Account: ${user?.full_name}`} aria-expanded={accountOpen} onClick={() => setAccountOpen((open) => !open)}><span className="avatar">{initials}</span><span className="account-name">{user?.full_name}</span></button>
          {accountOpen && <div className="account-menu"><div className="account-menu-user"><b>{user?.full_name}</b><small>{user?.email}</small><span>{user?.role}</span></div>{logoutError && <p className="account-error" role="alert">{logoutError}</p>}<button onClick={() => void signOut()} disabled={loggingOut}><LogOut size={15}/>{loggingOut ? 'Signing out…' : 'Sign out'}</button></div>}
        </div>
      </div></header>
      <main className="content">{children ?? <Outlet/>}</main>
      <footer className="footer"><span><span className="pulse-dot"/> AUTHENTICATED WORKSPACE</span><span>Evidence remains linked to its source document</span><span>v0.2.0</span></footer>
    </div>
  </div>;
}

function LoadingPanel() {
  return <div className="panel dashboard-loading" role="status"><span className="pulse-dot"/> Loading workspace data…</div>;
}

function QueryError({ onRetry }: { onRetry: () => void }) {
  return <div className="panel dashboard-error" role="alert"><div><b>Workspace data could not be loaded.</b><span>Check your connection and retry.</span></div><button className="button secondary" onClick={onRetry}>Retry</button></div>;
}

function StatusBadge({ value }: { value: string }) {
  const color = ['completed', 'ocr_completed', 'relationship_extraction_completed'].includes(value) ? 'green'
    : ['failed', 'relationship_extraction_failed'].includes(value) ? 'red'
      : ['processing', 'relationship_extraction_pending'].includes(value) ? 'amber'
        : value === 'queued' ? 'blue'
          : value === 'in_review' ? 'amber' : value === 'archived' ? 'neutral' : 'blue';
  const labels: Record<string, string> = {
    queued: 'Waiting for OCR',
    processing: 'OCR in progress',
    ocr_completed: 'OCR complete',
    relationship_extraction_pending: 'Finding relationships',
    relationship_extraction_completed: 'Analysis complete',
    relationship_extraction_failed: 'Relationship analysis failed',
    completed: 'Complete',
    failed: 'Needs attention',
  };
  const label = labels[value] ?? value.replaceAll('_', ' ');
  return <span className={`badge ${color}`}><span className="status-dot"/>{label}</span>;
}

function RecentActivityPanel({ items }: { items: ActivityItem[] }) {
  return <section className="panel activity-panel"><div className="panel-head"><div><h2>Recent activity</h2><p>Latest workspace events</p></div><Activity size={16} className="muted"/></div>
    {items.length ? <div className="activity-list">{items.map((item) => <article className="activity-item" key={item.id}><span className="activity-marker"><Activity size={13}/></span><div><b>{item.summary}</b><small>{item.case_number || 'Workspace'} · {fmtDate(item.created_at)}</small></div></article>)}</div> : <div className="empty-activity"><span>No recent activity yet.</span><small>Events will appear here as you create and update investigations.</small></div>}
  </section>;
}

export function DashboardPage() {
  const { user } = useAuth();
  const summary = useQuery({ queryKey: dashboardKeys.summary, queryFn: () => apiRequest<Summary>('/dashboard/summary') });
  const recent = useQuery({ queryKey: dashboardKeys.recent, queryFn: () => apiRequest<Investigation[]>('/dashboard/recent-investigations?limit=8') });
  const activity = useQuery({ queryKey: dashboardKeys.activity, queryFn: () => apiRequest<ActivityItem[]>('/dashboard/activity?limit=6') });
  const retry = () => { void Promise.all([summary.refetch(), recent.refetch(), activity.refetch()]); };
  const error = summary.isError || recent.isError || activity.isError;
  const loading = summary.isLoading || recent.isLoading || activity.isLoading;
  const canCreate = user?.permissions.includes('investigations:create') ?? false;
  const activeCasePath = recent.data?.[0]?.id ? `/investigations/${recent.data[0].id}` : '';
  const fallbackPath = canCreate ? '/investigations/new' : '/investigations';
  return <section className="dashboard-page">
    <section className="dashboard-hero" aria-labelledby="dashboard-title">
      <div className="dashboard-hero-copy">
        <div className="eyebrow"><Fingerprint size={14}/> DOCFORENSIQ FORENSIC WORKSPACE</div>
        <h2 className="dashboard-greeting">Good to have you back, {user?.full_name.split(' ')[0]}.</h2>
        <h1 id="dashboard-title">Beyond OCR. <span>Evidence-led document intelligence.</span></h1>
        <p>Investigate documents, compare evidence across a case, and keep every observation connected to its source.</p>
        <div className="hero-buttons">
          <Link className="button primary" to={fallbackPath}>{canCreate ? 'Start investigation' : 'Explore investigations'} <ArrowRight size={15}/></Link>
          <a className="button secondary" href="#workflow-cards">Explore workflow <ArrowRight size={14}/></a>
        </div>
      </div>
      <div className="dashboard-hero-visual" aria-hidden="true">
        <div className="forensic-document">
          <div className="forensic-document-head"><Fingerprint size={15}/><span>Source document</span><i/></div>
          <div className="forensic-document-lines"><i/><i/><i/><i/><i/><i/></div>
          <div className="forensic-region"><i/><i/><i/></div>
          <div className="forensic-document-footer"><span/><span/><span/></div>
        </div>
        <div className="forensic-lens"><Fingerprint size={28}/></div>
        <div className="forensic-index-mark">01 <span>Evidence in context</span></div>
      </div>
    </section>
    <div className="dashboard-greeting-row"><span>Your workspace, investigations, and recent activity.</span>{canCreate && <Link className="text-link" to="/investigations/new"><Plus size={14}/> New investigation</Link>}</div>
    <div className="stat-grid dashboard-stats">
      <article className="stat-card"><FileImage className="stat-icon" size={18}/><span className="stat-label">DOCUMENTS</span><strong>—</strong><small>Not available in the workspace summary</small></article>
      <article className="stat-card"><FileSearch className="stat-icon amber" size={18}/><span className="stat-label">CANDIDATE FINDINGS</span><strong>—</strong><small>Not available in the workspace summary</small></article>
      <article className="stat-card"><BriefcaseBusiness className="stat-icon" size={18}/><span className="stat-label">INVESTIGATIONS</span><strong>{summary.data?.total_investigations ?? '—'}</strong><small>Cases in your workspace</small></article>
      <article className="stat-card"><CheckCircle2 className="stat-icon lime" size={18}/><span className="stat-label">REVIEW COMPLETION</span><strong>—</strong><small>Not available in the workspace summary</small></article>
    </div>
    <section className="dashboard-workflows" id="workflow-cards" aria-labelledby="workflow-title">
      <div className="dashboard-section-heading"><div><span className="eyebrow">INVESTIGATOR WORKFLOW</span><h2 id="workflow-title">Evidence, from source to review</h2></div><Link className="text-link" to="/investigations">View investigations <ArrowRight size={14}/></Link></div>
      <div className="workflow-grid">
        <WorkflowCard icon={FileSearch} title="Intelligent Extraction" description="Inspect recognized content and source-linked regions." to={activeCasePath ? `${activeCasePath}/extracted-data` : fallbackPath}/>
        <WorkflowCard icon={FileImage} title="Visual Forensics" description="Review measured checks against stored originals." to={activeCasePath ? `${activeCasePath}/visual` : fallbackPath}/>
        <WorkflowCard icon={GitBranch} title="Cross-Document Analysis" description="Compare evidence and review candidate relationships." to={activeCasePath ? `${activeCasePath}/correlation` : fallbackPath}/>
        <WorkflowCard icon={Fingerprint} title="Evidence Traceability" description="Open source documents, pages, and cited regions." to={activeCasePath || fallbackPath}/>
        <WorkflowCard icon={Eye} title="Human Oversight" description="Review candidate findings and record dispositions." to={activeCasePath ? `${activeCasePath}/findings` : fallbackPath}/>
      </div>
    </section>
    {error ? <QueryError onRetry={retry}/> : loading ? <LoadingPanel/> : <>
      {(recent.data?.length ?? 0) === 0 ? <>
        <div className="panel dashboard-empty">
          <div className="empty-illustration"><BriefcaseBusiness size={21}/><Plus size={14}/></div><h2>Your workspace is ready</h2>
          <p>Start by creating an investigation. It will appear here with its case number and current status.</p>
          {canCreate && <Link className="button primary" to="/investigations/new"><Plus size={16}/> Create your first investigation</Link>}
        </div>
        {(activity.data?.length ?? 0) > 0 && <RecentActivityPanel items={activity.data ?? []}/>}
      </> : <div className="dashboard-columns">
        <section className="panel recent-panel"><div className="panel-head"><div><h2>Recent investigations</h2><p>Cases you own, ordered by latest update</p></div><Link className="text-link" to="/investigations">View all <ArrowRight size={13}/></Link></div>
          <div className="table-wrap"><table className="dashboard-table"><thead><tr><th>CASE ID</th><th>TITLE</th><th>STATUS</th><th>DOCUMENTS</th><th>UPDATED</th><th/></tr></thead><tbody>
            {recent.data?.map((item) => <tr key={item.id}><td><span className="case-id">{item.case_number}</span></td><td><b>{item.title}</b></td><td><StatusBadge value={item.status}/></td><td>{item.document_count}</td><td>{fmtDate(item.updated_at)}</td><td><Link className="text-link" aria-label={`Open ${item.case_number}`} to={`/investigations/${item.id}`}><ArrowRight size={15}/></Link></td></tr>)}
          </tbody></table></div>
        </section>
        <RecentActivityPanel items={activity.data ?? []}/>
      </div>}
    </>}
    <div className="dashboard-boundary"><ShieldCheck size={14}/><span>Document OCR is available for configured image uploads. Relationship extraction requires the OCR worker and a backend xAI key.</span></div>
  </section>;
}

function WorkflowCard({ icon: Icon, title, description, to }: { icon: LucideIcon; title: string; description: string; to: string }) {
  return <Link className="workflow-card" to={to}>
    <span className="workflow-icon"><Icon size={18}/></span>
    <span className="workflow-copy"><strong>{title}</strong><small>{description}</small></span>
    <ArrowRight className="workflow-arrow" size={15}/>
  </Link>;
}

export function InvestigationsPage() {
  const [status, setStatus] = useState('');
  const [searchParams] = useSearchParams();
  const searchText = searchParams.get('search')?.trim().toLocaleLowerCase() ?? '';
  const query = useQuery({ queryKey: ['investigations', status], queryFn: () => apiRequest<{ items: Investigation[] }>('/investigations?limit=100' + (status ? `&status=${status}` : '')) });
  const items = (query.data?.items ?? []).filter((item) => !searchText || `${item.case_number} ${item.title} ${item.status}`.toLocaleLowerCase().includes(searchText));
  return <section className="dashboard-page">
    <div className="dashboard-welcome"><div><div className="eyebrow"><BriefcaseBusiness size={13}/> WORKSPACE</div><h1>Investigations</h1><p>Browse and resume investigations you own.{searchText && <> Showing results for <strong>{searchParams.get('search')}</strong>.</>}</p></div><Link className="button primary" to="/investigations/new"><Plus size={16}/> New Investigation</Link></div>
    <div className="list-toolbar"><label className="auth-label">Filter by status<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option><option value="draft">Draft</option><option value="in_progress">In progress</option><option value="in_review">In review</option><option value="completed">Completed</option><option value="archived">Archived</option></select></label></div>
    {query.isLoading ? <LoadingPanel/> : query.isError ? <QueryError onRetry={() => void query.refetch()}/> : items.length === 0 ? <div className="panel dashboard-empty"><BriefcaseBusiness size={24}/><h2>{searchText ? 'No matching investigations' : 'No investigations found'}</h2><p>{searchText ? 'Try another search or clear the search field in the top bar.' : 'Create your first investigation or change the status filter.'}</p><Link className="button primary" to="/investigations/new"><Plus size={16}/> Create investigation</Link></div> :
      <section className="panel recent-panel"><div className="table-wrap"><table className="dashboard-table"><thead><tr><th>CASE ID</th><th>TITLE</th><th>STATUS</th><th>DOCUMENTS</th><th>UPDATED</th><th>ACTIONS</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><span className="case-id">{item.case_number}</span></td><td><b>{item.title}</b></td><td><StatusBadge value={item.status}/></td><td>{item.document_count}</td><td>{fmtDate(item.updated_at)}</td><td><div className="case-row-actions"><Link className="text-link" aria-label={`Open ${item.case_number}`} to={`/investigations/${item.id}`}>Documents</Link><Link className="text-link" to={`/investigations/${item.id}/relationships`}>Relationships</Link></div></td></tr>)}</tbody></table></div></section>}
  </section>;
}

export function NewInvestigationPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [error, setError] = useState('');
  const create = useMutation({
    mutationFn: () => apiRequest<Investigation>('/investigations', { method: 'POST', body: JSON.stringify({ title, description: description || null }) }),
    onSuccess: async (created) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
        queryClient.invalidateQueries({ queryKey: ['investigations'] }),
      ]);
      navigate(`/investigations/${created.id}`, { replace: true });
    },
  });
  async function submit(event: FormEvent) {
    event.preventDefault(); setError('');
    try { await create.mutateAsync(); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : 'Unable to create the investigation. Try again.'); }
  }
  return <section className="dashboard-page">
    <div className="dashboard-welcome"><div><div className="eyebrow"><Plus size={13}/> WORKSPACE</div><h1>New investigation</h1><p>Create a case record, then upload supported image documents for OCR processing.</p></div></div>
    <form className="panel create-form" onSubmit={submit}>
      <div className="panel-head"><div><h2>Investigation details</h2><p>A unique case number will be assigned by the server.</p></div></div>
      <label className="auth-label">Investigation title<input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} required placeholder="e.g. Vendor invoice review"/></label>
      <label className="auth-label">Description <span className="muted">(optional)</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={4000} rows={4} placeholder="Brief context for the investigation"/></label>
      <div className="create-warning"><FileText size={15}/> This creates metadata only. No documents are uploaded or analyzed.</div>
      {error && <div className="auth-error" role="alert">{error}</div>}
      <div className="create-actions"><Link className="button secondary" to="/">Cancel</Link><button className="button primary" disabled={create.isPending}><Plus size={15}/>{create.isPending ? 'Creating…' : 'Create investigation'}</button></div>
    </form>
  </section>;
}

type DocumentItem = RelationshipDocument & { attempts_remaining: number; source_description?: string | null; uploaded_by_user_id?: string | null };
type GraphResponse = {
  nodes: RelationshipEntity[];
  edges: RelationshipClaim[];
  graph_nodes?: ApiGraphNode[];
  graph_edges?: ApiGraphEdge[];
  meta: { investigation_id: string; graph_version: string };
};
type OCRResponse = { document_id: string; provider: string; provider_version: string | null; recognized_text: string; blocks: { block_id: string; source_path: string; text: string; page_index?: number; engine_bbox?: unknown; engine_confidence?: number }[]; raw_result?: unknown };
type ExtractionRun = { id: string; run_number: number; provider: string; provider_version: string | null; configuration: Record<string, unknown>; status: string; error_detail: string | null; latency_ms: number | null; started_at: string | null; completed_at: string | null };
type ExtractionDetail = ExtractionRun & { pages: Array<{ id: string; page_number: number; width: number | null; height: number | null; text: string }>; spans: Array<{ id: string; page_id: string | null; block_id: string; text: string; bounding_box: unknown; confidence: number | null; source_reference: string }> };

function boundingRect(value: unknown): { left: number; top: number; width: number; height: number } | null {
  if (!Array.isArray(value)) return null;
  const points: number[][] | null = value.length === 4 && value.every((item) => typeof item === 'number')
    ? [[value[0] as number, value[1] as number], [value[2] as number, value[3] as number]]
    : value.length >= 4 && value.every((item) => Array.isArray(item) && item.length >= 2 && item.every((coordinate) => typeof coordinate === 'number'))
      ? value as number[][]
      : null;
  if (!points) return null;
  const xs = points.map((point) => point[0]);
  const ys = points.map((point) => point[1]);
  const left = Math.min(...xs);
  const top = Math.min(...ys);
  const right = Math.max(...xs);
  const bottom = Math.max(...ys);
  return right > left && bottom > top ? { left, top, width: right - left, height: bottom - top } : null;
}

const ANALYSIS_SECTIONS = ['correlation', 'findings', 'timeline', 'visual', 'search', 'dossier', 'audit', 'ocr-evaluation'] as const;

function CaseNav({ investigationId, current }: { investigationId: string; current: string }) {
  const links: Array<[string, string]> = [
    ['Documents', ''],
    ['Relationships', 'relationships'],
    ['Cross-document', 'correlation'],
    ['Findings', 'findings'],
    ['Timeline', 'timeline'],
    ['Visual review', 'visual'],
    ['Search', 'search'],
    ['Dossier', 'dossier'],
    ['Audit', 'audit'],
    ['OCR evaluation', 'ocr-evaluation'],
  ];
  return <nav className="case-tabs" aria-label="Investigation sections">{links.map(([label, path]) => <NavLink key={path || 'documents'} end={path === ''} to={path ? `/investigations/${investigationId}/${path}` : `/investigations/${investigationId}`} className={current === (path || 'documents') ? 'active' : undefined}>{label}</NavLink>)}</nav>;
}

export function InvestigationUnavailable() {
  const { investigationId = '' } = useParams();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [error, setError] = useState('');
  const [uploadMessage, setUploadMessage] = useState('');
  const [uploading, setUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [selectedDocumentId, setSelectedDocumentId] = useState('');
  const [includeRaw, setIncludeRaw] = useState(false);
  const [relationshipSearch, setRelationshipSearch] = useState('');
  const [graphView, setGraphView] = useState<'graph' | 'table'>('graph');
  const [documentSearch, setDocumentSearch] = useState('');
  const [documentStatusFilter, setDocumentStatusFilter] = useState('');
  const [showRunHistory, setShowRunHistory] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState('');
  const [selectedBlockId, setSelectedBlockId] = useState('');
  const [regionSearch, setRegionSearch] = useState('');
  const [viewerZoom, setViewerZoom] = useState(100);
  const [uploadResults, setUploadResults] = useState<Array<{ filename: string; status: 'uploaded' | 'failed'; message: string }>>([]);
  const section = location.pathname.split('/').filter(Boolean).at(-1) === investigationId ? 'documents' : (location.pathname.split('/').filter(Boolean).at(-1) || 'documents');
  const relationshipsPage = section === 'relationships';
  const analysisSection = ANALYSIS_SECTIONS.includes(section as AnalysisSection) ? section as AnalysisSection : null;
  useEffect(() => {
    const requested = searchParams.get('doc');
    if (requested) setSelectedDocumentId(requested);
    const block = searchParams.get('block');
    if (block) setSelectedBlockId(block);
  }, [searchParams]);

  const investigation = useQuery({
    queryKey: ['investigation', investigationId],
    queryFn: () => apiRequest<Investigation>(`/investigations/${investigationId}`),
    enabled: Boolean(investigationId),
  });
  const documents = useQuery({
    queryKey: ['documents', investigationId],
    queryFn: () => apiRequest<DocumentItem[]>(`/investigations/${investigationId}/documents`),
    enabled: Boolean(investigationId),
    refetchInterval: (query) => Array.isArray(query.state.data) && query.state.data.some((doc) =>
      ['queued', 'processing', 'relationship_extraction_pending'].includes(doc.processing_status)) ? 2500 : false,
  });
  const documentItems = useMemo(() => Array.isArray(documents.data) ? documents.data : [], [documents.data]);
  const filteredDocuments = useMemo(() => documentItems.filter((doc) =>
    (!documentStatusFilter || doc.processing_status === documentStatusFilter)
    && (!documentSearch.trim() || `${doc.filename} ${doc.sha256 ?? ''} ${doc.source_description ?? ''}`.toLocaleLowerCase().includes(documentSearch.trim().toLocaleLowerCase())),
  ), [documentItems, documentSearch, documentStatusFilter]);
  const inProgressCount = documentItems.filter((doc) => ['queued', 'processing', 'relationship_extraction_pending'].includes(doc.processing_status)).length;
  const waitingCount = documentItems.filter((doc) => doc.processing_status === 'queued').length;
  const selectedDocument = documentItems.find((doc) => doc.id === selectedDocumentId);
  const ocrReady = selectedDocument && ['ocr_completed', 'relationship_extraction_pending', 'relationship_extraction_completed', 'relationship_extraction_failed', 'completed'].includes(selectedDocument.processing_status);
  const ocr = useQuery({
    queryKey: ['ocr', investigationId, selectedDocumentId, includeRaw],
    queryFn: () => apiRequest<OCRResponse>(`/investigations/${investigationId}/documents/${selectedDocumentId}/ocr${includeRaw ? '?include_raw=true' : ''}`),
    enabled: Boolean(selectedDocumentId && ocrReady && !relationshipsPage),
  });
  const extractionRuns = useQuery({
    queryKey: ['extraction-runs', investigationId, selectedDocumentId],
    queryFn: () => apiRequest<{ items: ExtractionRun[] }>(`/investigations/${investigationId}/documents/${selectedDocumentId}/extraction-runs`),
    enabled: Boolean(selectedDocumentId && !relationshipsPage),
  });
  const extractionDetail = useQuery({
    queryKey: ['extraction-run', investigationId, selectedDocumentId, selectedRunId],
    queryFn: () => apiRequest<ExtractionDetail>(`/investigations/${investigationId}/documents/${selectedDocumentId}/extraction-runs/${selectedRunId}`),
    enabled: Boolean(selectedDocumentId && selectedRunId && !relationshipsPage),
  });
  const sourceFile = useQuery({
    queryKey: ['source-file', investigationId, selectedDocumentId],
    queryFn: () => apiBlob(`/investigations/${investigationId}/documents/${selectedDocumentId}/file`),
    enabled: Boolean(selectedDocumentId && !relationshipsPage),
    retry: false,
  });
  const visualPage = Math.max(1, Number(searchParams.get('page')) || 1);
  const visualRegion = ['x0', 'y0', 'x1', 'y1'].every((key) => Number.isFinite(Number(searchParams.get(key))) && searchParams.get(key) !== null)
    ? { x0: Number(searchParams.get('x0')), y0: Number(searchParams.get('y0')), x1: Number(searchParams.get('x1')), y1: Number(searchParams.get('y1')) }
    : null;
  const sourcePagePreview = useQuery({
    queryKey: ['source-page-preview', investigationId, selectedDocumentId, visualPage],
    queryFn: () => apiBlob(`/investigations/${investigationId}/documents/${selectedDocumentId}/pages/${visualPage}/preview`),
    enabled: Boolean(selectedDocumentId && selectedDocument?.content_type === 'application/pdf' && visualRegion),
    retry: false,
  });
  const sourcePagePreviewUrl = useMemo(() => sourcePagePreview.data && typeof URL.createObjectURL === 'function' ? URL.createObjectURL(sourcePagePreview.data) : '', [sourcePagePreview.data]);
  useEffect(() => () => { if (sourcePagePreviewUrl) URL.revokeObjectURL(sourcePagePreviewUrl); }, [sourcePagePreviewUrl]);
  const sourcePreviewUrl = useMemo(() => sourceFile.data && typeof URL.createObjectURL === 'function' ? URL.createObjectURL(sourceFile.data) : '', [sourceFile.data]);
  useEffect(() => () => {
    if (sourcePreviewUrl && typeof URL.revokeObjectURL === 'function') URL.revokeObjectURL(sourcePreviewUrl);
  }, [sourcePreviewUrl]);
  useEffect(() => {
    if (!selectedBlockId) return;
    document.getElementById(`ocr-block-${selectedBlockId}`)?.scrollIntoView?.({ block: 'center' });
  }, [selectedBlockId, ocr.data]);
  useEffect(() => {
    if (!selectedRunId && extractionRuns.data?.items.length) setSelectedRunId(extractionRuns.data.items[0].id);
  }, [selectedRunId, extractionRuns.data]);
  const selectedPage = extractionDetail.data?.pages.find((page) => page.page_number === 1);
  const overlayPage = selectedPage && typeof selectedPage.width === 'number' && typeof selectedPage.height === 'number'
    ? { ...selectedPage, width: selectedPage.width, height: selectedPage.height }
    : null;
  const displayedBlocks = (ocr.data?.blocks ?? []).filter((block) =>
    !regionSearch.trim() || `${block.text} ${block.block_id}`.toLocaleLowerCase().includes(regionSearch.trim().toLocaleLowerCase()),
  );
  const documentStatus = useQuery({
    queryKey: ['document-status', investigationId, selectedDocumentId],
    queryFn: () => apiRequest<{ id: string; status: string; error: string | null }>(`/investigations/${investigationId}/documents/${selectedDocumentId}/status`),
    enabled: Boolean(selectedDocumentId && ['failed', 'relationship_extraction_failed'].includes(selectedDocument?.processing_status ?? '') && !relationshipsPage),
  });
  const graph = useQuery({
    queryKey: ['relationships', investigationId],
    queryFn: () => apiRequest<GraphResponse>(`/investigations/${investigationId}/relationships/graph`),
    enabled: relationshipsPage,
    refetchInterval: () => documentItems.some((doc) => ['queued', 'processing', 'relationship_extraction_pending'].includes(doc.processing_status)) ? 2500 : false,
  });
  async function uploadFiles(files: File[], form: HTMLFormElement) {
    if (!files.length) return;
    setError('');
    setUploadMessage('');
    setUploading(true);
    const sourceDescription = String(new FormData(form).get('source_description') ?? '');
    try {
      const uploaded: { id: string; filename: string }[] = [];
      const failures: string[] = [];
      for (const file of files) {
        try {
          const created = await apiRequest<{ id: string; filename: string }>(`/investigations/${investigationId}/documents`, {
            method: 'POST',
            headers: {
              'Content-Type': file.type,
              'X-File-Name': encodeURIComponent(file.name),
              'X-Source-Description': sourceDescription,
            },
            body: file,
          });
          uploaded.push({ id: created.id, filename: created.filename });
          setUploadResults((current) => [...current, { filename: file.name, status: 'uploaded', message: 'Uploaded; OCR queued.' }]);
        } catch (reason) {
          const message = reason instanceof ApiError ? reason.message : 'Upload failed.';
          failures.push(`${file.name}: ${message}`);
          setUploadResults((current) => [...current, { filename: file.name, status: 'failed', message }]);
        }
      }
      if (uploaded.length) {
        form.reset();
        setSelectedDocumentId(uploaded[uploaded.length - 1].id);
        setUploadMessage(`${uploaded.length} of ${files.length} file(s) uploaded. OCR starts automatically for each accepted document.`);
        await queryClient.invalidateQueries({ queryKey: ['documents', investigationId] });
      }
      if (failures.length) setError(failures.join(' '));
    } finally {
      setUploading(false);
    }
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const files = Array.from(form.querySelector<HTMLInputElement>('input[type="file"]')?.files ?? []);
    await uploadFiles(files, form);
  }

  function handleDrop(event: DragEvent<HTMLFormElement>) {
    event.preventDefault();
    setDragActive(false);
    if (uploading) return;
    void uploadFiles(Array.from(event.dataTransfer.files), event.currentTarget);
  }

  async function retryDocument(documentId: string) {
    setError('');
    try {
      await apiRequest(`/investigations/${investigationId}/documents/${documentId}/retry`, { method: 'POST' });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['documents', investigationId] }),
        queryClient.invalidateQueries({ queryKey: ['ocr', investigationId, documentId] }),
      ]);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'The document could not be retried.');
    }
  }

  if (analysisSection) {
    return <section className="dashboard-page">
      <div className="dashboard-welcome"><div><div className="eyebrow"><FileText size={13}/> {investigation.data?.case_number || 'INVESTIGATION'}</div><h1>{investigation.data?.title || 'Investigation'}</h1><p>Evidence-backed review. Automated results stay candidates until you record a disposition.</p></div></div>
      <CaseNav investigationId={investigationId} current={analysisSection}/>
      <InvestigationAnalysis investigationId={investigationId} section={analysisSection}/>
    </section>;
  }

  if (relationshipsPage) {
    return <section className="dashboard-page">
      <div className="dashboard-welcome"><div><div className="eyebrow"><Network size={13}/> RELATIONSHIP ANALYSIS</div><h1>{investigation.data?.title || 'Investigation relationships'}</h1><p>Grounded entity and relationship claims · {investigation.data?.case_number || ''}</p></div>
        <div className="create-actions"><Link className="button secondary" to={`/investigations/${investigationId}`}>Documents</Link></div></div>
      <CaseNav investigationId={investigationId} current="relationships"/>
      <div className="list-toolbar"><div className="relationship-controls" role="group" aria-label="Relationship view">
        <button type="button" className="button secondary small" aria-pressed={graphView === 'graph'} onClick={() => setGraphView('graph')}><Network size={13}/> Graph</button>
        <button type="button" className="button secondary small" aria-pressed={graphView === 'table'} onClick={() => setGraphView('table')}>Table</button>
      </div></div>
      {graph.isLoading ? <LoadingPanel/> : graph.isError ? <QueryError onRetry={() => void graph.refetch()}/> :
        graphView === 'table' ? (
        <RelationshipExplorer
          documents={documentItems}
          entities={graph.data?.nodes ?? []}
          claims={graph.data?.edges ?? []}
          search={relationshipSearch}
          onSearch={setRelationshipSearch}
          onOpenDocument={(documentId) => {
            setSelectedDocumentId(documentId);
            setIncludeRaw(false);
            navigate(`/investigations/${investigationId}`);
          }}
          initialTableView
        />) : (
        <EvidenceGraphView
          documents={documentItems}
          entities={graph.data?.nodes ?? []}
          claims={graph.data?.edges ?? []}
          graphNodes={graph.data?.graph_nodes}
          graphEdges={graph.data?.graph_edges}
          search={relationshipSearch}
          onSearch={setRelationshipSearch}
          onOpenDocument={(documentId) => {
            setSelectedDocumentId(documentId);
            setIncludeRaw(false);
            navigate(`/investigations/${investigationId}`);
          }}
          onGoToDocuments={() => navigate(`/investigations/${investigationId}`)}
        />)}
    </section>;
  }

  return <section className="dashboard-page">
    <div className="dashboard-welcome"><div><div className="eyebrow"><BriefcaseBusiness size={13}/> INVESTIGATION</div><h1>{investigation.data?.title || 'Investigation'}</h1><p>{investigation.data?.case_number || 'Loading case details'} · Upload source images for OCR processing.</p></div><div className="create-actions"><Link className="button secondary" to="/investigations">Back to investigations</Link></div></div>
    <CaseNav investigationId={investigationId} current="documents"/>
    <form className={`panel create-form ${dragActive ? 'drag-active' : ''}`} onSubmit={(event) => void upload(event)} onDragOver={(event) => { event.preventDefault(); if (!uploading) setDragActive(true); }} onDragLeave={(event) => { if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setDragActive(false); }} onDrop={handleDrop}><div className="panel-head"><div><h2>Source documents</h2><p>Choose one or more JPEG, PNG, TIFF, or PDF files up to 25 MiB each. Originals are stored unchanged; OCR starts automatically.</p></div></div><label className="auth-label">Source / provenance note<input name="source_description" maxLength={2000} placeholder="Where the evidence came from (optional)"/></label><div className="upload-dropzone"><div className="create-actions"><input aria-label="Choose document files" name="file" type="file" accept="image/jpeg,image/png,image/tiff,application/pdf,.jpg,.jpeg,.png,.tif,.tiff,.pdf" multiple required/><button className="button primary" disabled={uploading}><Plus size={15}/>{uploading ? 'Uploading selected files…' : 'Upload and start OCR'}</button></div><p className="dropzone-hint" aria-hidden="true">{dragActive ? 'Drop files to upload' : '…or drag & drop files anywhere in this panel'}</p></div>{uploadMessage && <div className="upload-success" role="status" aria-live="polite"><CheckCircle2 size={15}/><span>{uploadMessage}</span></div>}{uploadResults.length > 0 && <ul aria-label="Upload results" className="upload-result-list">{uploadResults.slice(-8).map((result, index) => <li key={`${result.filename}-${index}`}><b>{result.filename}</b><span>{result.status === 'uploaded' ? result.message : `Failed: ${result.message}`}</span></li>)}</ul>}{error && <div className="auth-error" role="alert">{error}</div>}</form>
    {documents.isLoading ? <LoadingPanel/> : documents.isError ? <QueryError onRetry={() => void documents.refetch()}/> : <section className="panel recent-panel"><div className="panel-head"><div><h2>Evidence inventory</h2><p>{inProgressCount ? `OCR is processing ${inProgressCount} document(s)${waitingCount ? ` · ${waitingCount} queued` : ''}. Status refreshes as work completes.` : 'Source metadata and integrity hashes are recorded for each upload.'}</p></div></div><div className="list-toolbar"><label className="auth-label">Search evidence<input value={documentSearch} onChange={(event) => setDocumentSearch(event.target.value)} placeholder="Filename, hash, or source"/></label><label className="auth-label">Processing status<select value={documentStatusFilter} onChange={(event) => setDocumentStatusFilter(event.target.value)}><option value="">All statuses</option>{[...new Set(documentItems.map((doc) => doc.processing_status))].sort().map((status) => <option key={status} value={status}>{status.replaceAll('_', ' ')}</option>)}</select></label></div>{filteredDocuments.length ? <div className="table-wrap"><table className="dashboard-table"><thead><tr><th>FILE</th><th>SHA-256</th><th>SOURCE</th><th>SIZE</th><th>STATUS</th><th>ADDED</th><th/></tr></thead><tbody>{filteredDocuments.map((doc) => <tr key={doc.id}><td><button className="text-link" onClick={() => { setSelectedDocumentId(doc.id); setIncludeRaw(false); setSelectedRunId(''); }}>{doc.filename}</button></td><td><code>{doc.sha256 ? `${doc.sha256.slice(0, 12)}…` : 'Unavailable'}</code></td><td>{doc.source_description || 'Not recorded'}</td><td>{(doc.size_bytes / 1024 / 1024).toFixed(2)} MiB</td><td><StatusBadge value={doc.processing_status}/></td><td>{fmtDate(doc.created_at)}</td><td>{['failed', 'relationship_extraction_failed'].includes(doc.processing_status) && (doc.attempts_remaining > 0 ? <button className="text-link" onClick={() => void retryDocument(doc.id)}>Retry · {doc.attempts_remaining} left</button> : <span className="muted">No retries left</span>)}</td></tr>)}</tbody></table></div> : <p className="panel-foot">{documentItems.length ? 'No documents match these filters.' : 'No documents uploaded yet.'}</p>}</section>}
    {selectedDocument && <section className="panel recent-panel"><div className="panel-head"><div><h2>{selectedDocument.filename}</h2><p><StatusBadge value={selectedDocument.processing_status}/></p></div><button className="button secondary small" type="button" aria-expanded={showRunHistory} onClick={() => setShowRunHistory((value) => !value)}>{showRunHistory ? 'Hide extraction history' : 'Extraction history'}</button></div>
      {sourceFile.isError && <p className="notice" role="status">Original preview is unavailable. The OCR text and evidence references remain available.</p>}
      {sourcePreviewUrl && selectedDocument.content_type === 'application/pdf' && visualRegion && sourcePagePreviewUrl ? <div className="evidence-pdf-region-preview"><div className="pdf-region-canvas"><img src={sourcePagePreviewUrl} alt={`Rendered source page ${visualPage} of ${selectedDocument.filename}`}/><div className="visual-evidence-region" style={{ left: `${visualRegion.x0 * 1.5}px`, top: `${visualRegion.y0 * 1.5}px`, width: `${(visualRegion.x1 - visualRegion.x0) * 1.5}px`, height: `${(visualRegion.y1 - visualRegion.y0) * 1.5}px` }}/></div><p>Source page {visualPage} · region highlighted in rendered source coordinates. The original PDF remains available below.</p><iframe className="evidence-source-frame" title={`Original source PDF: ${selectedDocument.filename}`} src={`${sourcePreviewUrl}#page=${visualPage}`}/></div> : sourcePreviewUrl && selectedDocument.content_type === 'application/pdf' && <iframe className="evidence-source-frame" title={`Original source PDF: ${selectedDocument.filename}`} src={`${sourcePreviewUrl}#page=${visualPage}`}/>}
      {sourcePreviewUrl && selectedDocument.content_type.startsWith('image/') && <div className="evidence-viewer">
        <div className="evidence-viewer-toolbar" role="group" aria-label="Source viewer controls"><button type="button" className="button secondary small" aria-label="Zoom out source" onClick={() => setViewerZoom((value) => Math.max(50, value - 10))}>−</button><span aria-live="polite">{viewerZoom}%</span><button type="button" className="button secondary small" aria-label="Zoom in source" onClick={() => setViewerZoom((value) => Math.min(200, value + 10))}>+</button><button type="button" className="button secondary small" onClick={() => setViewerZoom(100)}>Fit</button></div>
        <div className="evidence-source-preview"><div className="evidence-source-page" style={{ width: `${viewerZoom}%` }}><img src={sourcePreviewUrl} alt={`Unmodified source document ${selectedDocument.filename}`}/>{visualRegion && overlayPage && visualPage === 1 && <div className="visual-evidence-region" style={{ left: `${visualRegion.x0 / overlayPage.width * 100}%`, top: `${visualRegion.y0 / overlayPage.height * 100}%`, width: `${(visualRegion.x1 - visualRegion.x0) / overlayPage.width * 100}%`, height: `${(visualRegion.y1 - visualRegion.y0) / overlayPage.height * 100}%` }}/>}
          {overlayPage && displayedBlocks.map((block) => {
            const rect = boundingRect(block.engine_bbox);
            if (!rect || (block.page_index ?? 0) !== 0) return null;
            return <button key={block.block_id} type="button" aria-label={`Highlight OCR region ${block.block_id}: ${block.text}`} className={`ocr-region-overlay ${selectedBlockId === block.block_id ? 'is-selected' : ''}`} style={{ left: `${rect.left / overlayPage.width * 100}%`, top: `${rect.top / overlayPage.height * 100}%`, width: `${rect.width / overlayPage.width * 100}%`, height: `${rect.height / overlayPage.height * 100}%` }} onClick={() => setSelectedBlockId(block.block_id)}/>;
          })}
        </div></div>
        {overlayPage && <p className="muted">Region overlays use original-image coordinates from OCR · Page dimensions {overlayPage.width} × {overlayPage.height}.</p>}
      </div>}
      {selectedDocument.sha256 && <p className="muted">Original SHA-256: <code>{selectedDocument.sha256}</code> · uploaded {fmtDate(selectedDocument.created_at)} · {selectedDocument.source_description || 'source not described'}</p>}
      {showRunHistory && <section className="panel" aria-label="OCR extraction history"><div className="panel-head"><div><h3>Versioned OCR extraction runs</h3><p>Each run retains its engine output and page/span references; older results are not overwritten.</p></div></div>{extractionRuns.isLoading ? <p role="status">Loading extraction runs…</p> : extractionRuns.isError ? <p role="alert">Extraction history could not be loaded.</p> : (extractionRuns.data?.items.length ?? 0) === 0 ? <p>No versioned extraction runs are available for this document yet.</p> : <><label className="auth-label">Compare with run<select aria-label="Select OCR run to inspect" value={selectedRunId || extractionRuns.data?.items[0]?.id || ''} onChange={(event) => setSelectedRunId(event.target.value)}>{extractionRuns.data?.items.map((run) => <option key={run.id} value={run.id}>Run {run.run_number} · {run.provider} {run.provider_version || ''} · {run.status}</option>)}</select></label><ul className="extraction-run-list">{extractionRuns.data?.items.map((run) => <li key={run.id}>Run {run.run_number} · {run.status} · {run.latency_ms == null ? 'duration unavailable' : `${run.latency_ms.toFixed(0)} ms`}{run.error_detail ? ` · ${run.error_detail}` : ''}</li>)}</ul>{extractionDetail.data && <div className="extraction-page-list"><h4>Run {extractionDetail.data.run_number} normalized pages and regions</h4>{extractionDetail.data.pages.map((page) => <article key={page.id}><b>Page {page.page_number}</b><small>{page.width && page.height ? `${page.width} × ${page.height}` : 'Page dimensions unavailable'}</small><pre>{page.text}</pre></article>)}</div>}</>}</section>}
      {['queued', 'processing', 'relationship_extraction_pending'].includes(selectedDocument.processing_status) && <p className="processing-help" role="status" aria-live="polite">{selectedDocument.processing_status === 'queued' ? 'Your upload is safe. The OCR worker will pick it up automatically as soon as it finishes the document ahead of it.' : selectedDocument.processing_status === 'processing' ? 'PaddleOCR is reading your image now. On the first run, OCR may take a few minutes while its models initialize.' : 'OCR is complete. Relationship analysis is now running automatically.'}</p>}
      {['failed', 'relationship_extraction_failed'].includes(selectedDocument.processing_status) && <p className="auth-error" role="alert">{documentStatus.data?.error ?? 'Processing failed. You can retry this document.'}</p>}
      {ocrReady && (ocr.isLoading ? <LoadingPanel/> : ocr.isError ? <QueryError onRetry={() => void ocr.refetch()}/> : <><div className="panel-head"><div><h3>Recognized text</h3><p>{ocr.data?.provider} {ocr.data?.provider_version ? `v${ocr.data.provider_version}` : ''} · {ocr.data?.blocks.length ?? 0} source blocks</p></div><button className="button secondary small" onClick={() => setIncludeRaw((value) => !value)}>{includeRaw ? 'Hide raw engine output' : 'Show raw engine output'}</button></div><pre className="ocr-text">{ocr.data?.recognized_text || 'No text was recognized.'}</pre><label className="auth-label">Search OCR regions<input value={regionSearch} onChange={(event) => setRegionSearch(event.target.value)} placeholder="Search text or block ID"/></label><div className="ocr-block-list">{displayedBlocks.map((block) => <button id={`ocr-block-${block.block_id}`} type="button" className={`ocr-region-card ${selectedBlockId === block.block_id ? 'is-selected' : ''}`} key={block.block_id} onClick={() => setSelectedBlockId(block.block_id)}><b>{block.block_id}</b><small>{block.page_index == null ? block.source_path : `Page ${block.page_index + 1} · ${block.source_path}`}</small><p>{block.text}</p>{block.engine_bbox != null && <small>Bounds: {JSON.stringify(block.engine_bbox)}{block.engine_confidence == null ? '' : ` · engine confidence ${block.engine_confidence} (not calibrated accuracy)`}</small>}</button>)}</div>{includeRaw && <pre className="ocr-text">{JSON.stringify(ocr.data?.raw_result, null, 2)}</pre>}</>)}
    </section>}
    <div className="dashboard-boundary"><ShieldCheck size={14}/><span>OCR output preserves the engine JSON and source block references. Relationship claims are descriptive and cite their source blocks.</span></div>
  </section>;
}
