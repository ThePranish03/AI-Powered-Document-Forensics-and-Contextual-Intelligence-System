import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import EvidenceGraphView from './EvidenceGraphView';
import type { ApiGraphEdge, ApiGraphNode } from './graphAdapter';
import type { RelationshipDocument } from './RelationshipExplorer';

const documents: RelationshipDocument[] = [{
  id: 'doc-1',
  filename: 'invoice.png',
  content_type: 'image/png',
  size_bytes: 12,
  processing_status: 'ocr_completed',
  created_at: '2025-01-01T00:00:00Z',
}];
const graphNodes: ApiGraphNode[] = [
  { id: 'document:doc-1', kind: 'document', label: 'invoice.png', document_id: 'doc-1' },
  { id: 'finding:f-1', kind: 'finding', label: 'Unusual invoice amount', type: 'amount_anomaly', status: 'candidate', confidence: 0.81 },
];
const graphEdges: ApiGraphEdge[] = [{
  id: 'finding-evidence:f-1:0',
  source: 'document:doc-1',
  target: 'finding:f-1',
  relationship_type: 'supports',
  label: 'cited by finding',
  status: 'candidate',
  confidence: 0.81,
  basis: 'Finding cites source text.',
  analyzer: 'deterministic',
  evidence_refs: [{ document_id: 'doc-1', filename: 'invoice.png', block_id: 'p1-b2', page_number: 0, quoted_text: 'Total: $9,900' }],
}];

describe('EvidenceGraphView', () => {
  it('shows graph-v2 findings and opens their cited source document', () => {
    const onOpenDocument = vi.fn();
    render(
      <EvidenceGraphView documents={documents} entities={[]} claims={[]} graphNodes={graphNodes} graphEdges={graphEdges}
        search="" onSearch={vi.fn()} onOpenDocument={onOpenDocument} />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Finding: Unusual invoice amount/i }));
    expect(screen.getByText('FINDING DETAILS')).toBeInTheDocument();
    expect(screen.getByText(/81% · uncalibrated/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'cited by finding: document:doc-1 to finding:f-1' }));
    expect(screen.getByText('Total: $9,900')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'invoice.png' }));
    expect(onOpenDocument).toHaveBeenCalledWith('doc-1');
  });

  it('clears the current selection back to the inspect prompt', () => {
    render(
      <EvidenceGraphView documents={documents} entities={[]} claims={[]} graphNodes={graphNodes} graphEdges={graphEdges}
        search="" onSearch={vi.fn()} onOpenDocument={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Finding: Unusual invoice amount/i }));
    expect(screen.getByText('FINDING DETAILS')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Clear selection' }));
    expect(screen.getByRole('heading', { name: 'Inspect graph evidence' })).toBeInTheDocument();
    expect(screen.queryByText('FINDING DETAILS')).not.toBeInTheDocument();
  });

  it('explains an empty investigation and offers a next step to documents', () => {
    const onGoToDocuments = vi.fn();
    render(
      <EvidenceGraphView documents={[]} entities={[]} claims={[]}
        search="" onSearch={vi.fn()} onOpenDocument={vi.fn()} onGoToDocuments={onGoToDocuments} />,
    );
    expect(screen.getByRole('heading', { name: 'No evidence connected yet' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Go to documents' }));
    expect(onGoToDocuments).toHaveBeenCalled();
  });

  it('offers to clear filters when they hide every node', () => {
    const onSearch = vi.fn();
    render(
      <EvidenceGraphView documents={documents} entities={[]} claims={[]} graphNodes={graphNodes} graphEdges={graphEdges}
        search="" onSearch={onSearch} onOpenDocument={vi.fn()} />,
    );
    fireEvent.change(screen.getByLabelText('Node type'), { target: { value: 'entity' } });
    expect(screen.getByRole('heading', { name: 'No matching graph nodes' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(onSearch).toHaveBeenCalledWith('');
    expect(screen.getByRole('button', { name: /Finding: Unusual invoice amount/i })).toBeInTheDocument();
  });
});
