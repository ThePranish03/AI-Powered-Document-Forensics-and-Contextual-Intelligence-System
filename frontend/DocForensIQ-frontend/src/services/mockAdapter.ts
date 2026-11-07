import { auditSeed, documents, fields, findings, standards, stages, timeline } from '../data';

/** Deterministic frontend adapter. Replace these exports with API calls when a backend exists. */
export const mockAdapter = {
  getInvestigations: () => [{ id: 'DF-2024-00421', title: 'Income verification · Rahul Sharma', status: 'In review', documents: documents.length }],
  getDocuments: () => documents,
  getFields: () => fields,
  getFindings: () => findings,
  getAuditEvents: () => auditSeed,
  getProcessingStages: () => stages,
  getTimeline: () => timeline,
  getStandards: () => standards,
  mode: 'synthetic-demo' as const,
};
