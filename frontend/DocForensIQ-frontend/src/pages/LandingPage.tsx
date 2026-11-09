import { ArrowRight, ArrowUpRight, BookOpen, FileImage, FileSearch, Fingerprint, GitBranch, Network, Play, ShieldCheck } from 'lucide-react';
import { Link } from 'react-router-dom';
import '../landing.css';

const capabilities = [
  {
    icon: FileSearch,
    title: 'Keep extracted details tied to their source',
    description: 'Move from a recognized value to the page and region it came from.',
    href: '/investigations',
  },
  {
    icon: GitBranch,
    title: 'Compare records across a case',
    description: 'Review agreements and differences without losing document context.',
    href: '/investigations',
  },
  {
    icon: FileImage,
    title: 'Inspect the original document',
    description: 'Keep visual review close to extracted text and investigator notes.',
    href: '/investigations',
  },
];

const pillars = [
  { icon: FileSearch, label: 'Intelligent Extraction' },
  { icon: FileImage, label: 'Visual Forensics' },
  { icon: GitBranch, label: 'Cross-Document Correlation' },
  { icon: Network, label: 'Contextual Analysis' },
  { icon: BookOpen, label: 'Evidence Dossier' },
];

export default function LandingPage() {
  return (
    <main className="marketing-page" id="top">
      <header className="marketing-nav">
        <Link className="marketing-brand" to="/" aria-label="DocForensIQ home">
          <span className="marketing-brand-mark"><Fingerprint size={19} strokeWidth={1.8} /></span>
          <span>DocForens<span className="marketing-brand-accent">IQ</span></span>
        </Link>
        <nav className="marketing-links" aria-label="Main navigation">
          <a href="#capabilities">Capabilities</a>
          <a href="#workflow">Workflow</a>
          <a href="#method">Method</a>
        </nav>
        <Link className="marketing-signin" to="/login">Sign in <ArrowUpRight size={15} /></Link>
      </header>

      <div className="marketing-content">
        <section className="marketing-hero" aria-labelledby="landing-title">
          <div className="marketing-hero-copy">
            <p className="marketing-kicker">Evidence-led document intelligence</p>
            <h1 id="landing-title">Beyond OCR.<br /><span>Evidence-led</span><br />document intelligence.</h1>
            <p className="marketing-lede">
              Investigate documents, surface inconsistencies, correlate evidence, and prepare explainable findings with human review at every consequential step.
            </p>
            <div className="marketing-hero-actions">
              <Link className="marketing-primary-link" to="/investigations/new">Start Investigation <ArrowRight size={17} /></Link>
              <a className="marketing-secondary-link" href="#workflow"><Play size={15} /> Explore the workflow</a>
            </div>
            <div className="marketing-proof" aria-label="Illustrative platform metrics">
              <div><strong>05</strong><span>Synthetic documents</span></div>
              <i aria-hidden="true"/><div><strong>04</strong><span>Illustrative findings</span></div>
              <i aria-hidden="true"/><div><strong>100%</strong><span>Human review</span></div>
            </div>
          </div>
          <figure className="marketing-hero-visual">
            <img
              src="/landing-assets/06_Evidence_Viewer.png"
              width="541"
              height="228"
              fetchPriority="high"
              decoding="async"
              alt="DocForensIQ evidence viewer preview with an original document, highlighted value, and linked finding. Sample data is synthetic."
            />
            <figcaption><span>Evidence Viewer</span><span>Illustrative interface with synthetic records</span></figcaption>
          </figure>
        </section>

        <section className="marketing-pillars" aria-label="Platform capabilities">
          {pillars.map(({ icon: Icon, label }) => (
            <div className="marketing-pillar" key={label}>
              <span className="marketing-pillar-icon"><Icon size={17} strokeWidth={1.7} /></span>
              <span>{label}</span>
            </div>
          ))}
        </section>

        <section className="marketing-capabilities" id="capabilities" aria-labelledby="capabilities-title">
          <div className="marketing-section-intro">
            <h2 id="capabilities-title">Every observation has a way back to its source.</h2>
            <p>Move from extracted details to cross-document context without treating an automated result as a conclusion.</p>
          </div>
          <div className="marketing-capability-list">
            {capabilities.map(({ icon: Icon, title, description, href }) => (
              <Link className="marketing-capability" key={title} to={href}>
                <span className="marketing-capability-icon"><Icon size={18} strokeWidth={1.7} /></span>
                <div><h3>{title}</h3><p>{description}</p></div>
                <ArrowUpRight className="marketing-capability-arrow" size={16} />
              </Link>
            ))}
          </div>
        </section>

        <section className="marketing-workflow" id="workflow" aria-labelledby="workflow-title">
          <div className="marketing-workflow-heading">
            <p className="marketing-kicker">From page to case context</p>
            <h2 id="workflow-title">Read the document.<br />Then read across the case.</h2>
          </div>
          <div className="marketing-preview-grid">
            <figure className="marketing-preview marketing-preview-wide">
              <img
                src="/landing-assets/07_Cross_Document_Comparison.png"
                width="603"
                height="228"
                loading="lazy"
                decoding="async"
                alt="Cross-document comparison preview showing fields compared across several sample records."
              />
              <figcaption><strong>Compare across documents</strong><span>Differences remain visible beside their source fields.</span></figcaption>
            </figure>
            <figure className="marketing-preview marketing-preview-tall">
              <img
                src="/landing-assets/08_Relationship_Graph.png"
                width="471"
                height="205"
                loading="lazy"
                decoding="async"
                alt="Relationship map preview connecting sample entities to the documents that mention them."
              />
              <figcaption><strong>Follow documented connections</strong><span>Explore linked records with their evidence references.</span></figcaption>
            </figure>
          </div>
          <p className="marketing-sample-note">Product previews contain synthetic sample data and are not real case evidence.</p>
        </section>

        <section className="marketing-method" id="method" aria-labelledby="method-title">
          <div className="marketing-method-mark"><ShieldCheck size={21} /></div>
          <div>
            <h2 id="method-title">Support a decision. Never substitute for one.</h2>
            <p>DocForensIQ is a document review and decision-support interface. It does not authenticate documents, determine fraud, or replace a validated forensic process.</p>
          </div>
        </section>
      </div>

      <footer className="marketing-footer">
        <Link className="marketing-brand marketing-footer-brand" to="/">
          <span className="marketing-brand-mark"><Fingerprint size={17} strokeWidth={1.8} /></span>
          <span>DocForens<span className="marketing-brand-accent">IQ</span></span>
        </Link>
        <span>Evidence-led document intelligence</span>
        <a href="#top">Back to top <ArrowUpRight size={14} /></a>
      </footer>
    </main>
  );
}
