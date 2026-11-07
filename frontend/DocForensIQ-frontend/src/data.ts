export type FindingLabel = 'Observed characteristic' | 'Detected inconsistency' | 'Potential manipulation';
export type ReviewState = 'Unreviewed' | 'Accepted' | 'Dismissed' | 'Needs verification';
export interface Anchor { documentId: string; documentName: string; pageNumber: number; box: { x: number; y: number; width: number; height: number }; text: string; hash: string; method: string; }
export interface Finding { id: string; title: string; label: FindingLabel; severity: 'High'|'Medium'|'Low'|'Informational'; confidence: number; summary: string; method: string; status: ReviewState; evidence: Anchor[]; alternative: string; }
export interface DocumentRecord { id: string; name: string; kind: string; pages: number; size: string; hash: string; status: string; }
export interface FieldRecord { id:string; name:string; value:string; confidence:number; ocr:number; anchor:Anchor; review:string; }
export interface AuditRecord { time:string; actor:string; action:string; detail:string; }
export const documents: DocumentRecord[] = [
 {id:'DOC-8F31A2',name:'Rahul_Sharma_ID.pdf',kind:'Identity document',pages:2,size:'1.8 MB',hash:'9f2a…c814',status:'Indexed · demo'},
 {id:'DOC-4C90DE',name:'Salary_Slip_Oct.pdf',kind:'Salary slip',pages:1,size:'842 KB',hash:'b71c…09e2',status:'Indexed · demo'},
 {id:'DOC-2A61F7',name:'Bank_Statement_Q3.pdf',kind:'Bank statement',pages:4,size:'2.4 MB',hash:'e38d…6b12',status:'Indexed · demo'},
 {id:'DOC-A5D2B9',name:'Loan_Application.pdf',kind:'Loan application',pages:3,size:'1.1 MB',hash:'31af…de71',status:'Partial · 1 page needs review'},
 {id:'DOC-C71E04',name:'Address_Proof.jpg',kind:'Address proof',pages:1,size:'526 KB',hash:'5d00…f347',status:'Indexed · demo'}
];
const anchor=(d:DocumentRecord,p:number,text:string):Anchor=>({documentId:d.id,documentName:d.name,pageNumber:p,box:{x:.56,y:.62,width:.27,height:.08},text,hash:d.hash,method:'Demo extraction adapter · v0.1'});
export const fields:FieldRecord[]=[
 {id:'F-01',name:'Full name',value:'Rahul Sharma',confidence:.99,ocr:.98,anchor:anchor(documents[0],1,'Rahul Sharma'),review:'Reviewed'},
 {id:'F-02',name:'Date of birth',value:'12-05-1998',confidence:.97,ocr:.94,anchor:anchor(documents[0],1,'12-05-1998'),review:'Reviewed'},
 {id:'F-03',name:'Address',value:'Nagpur, Maharashtra',confidence:.91,ocr:.89,anchor:anchor(documents[4],1,'Nagpur, Maharashtra'),review:'Unreviewed'},
 {id:'F-04',name:'Employer',value:'ABC Technologies Pvt. Ltd.',confidence:.96,ocr:.96,anchor:anchor(documents[1],1,'ABC Technologies Pvt. Ltd.'),review:'Reviewed'},
 {id:'F-05',name:'Monthly salary',value:'₹45,000',confidence:.92,ocr:.95,anchor:anchor(documents[1],1,'₹45,000'),review:'Needs review'},
 {id:'F-06',name:'Monthly salary',value:'₹38,000',confidence:.88,ocr:.93,anchor:anchor(documents[2],2,'₹38,000'),review:'Needs review'},
 {id:'F-07',name:'Account number',value:'XXXX 4567',confidence:.84,ocr:.81,anchor:anchor(documents[2],1,'XXXX 4567'),review:'Unreviewed'},
 {id:'F-08',name:'Application amount',value:'₹18,00,000',confidence:.97,ocr:.97,anchor:anchor(documents[3],1,'₹18,00,000'),review:'Reviewed'}
];
export const findings:Finding[]=[
 {id:'FND-001',title:'Salary amount differs across source documents',label:'Detected inconsistency',severity:'High',confidence:.92,summary:'The salary slip records ₹45,000 while the bank statement shows a recurring monthly credit of ₹38,000. Values and periods require investigator context.',method:'Adaptive cross-document field comparison · demo rule set v0.1',status:'Unreviewed',evidence:[{...fields[4].anchor},{...fields[5].anchor}],alternative:'The difference may reflect deductions, a partial month, or a different credit schedule; the supplied excerpts do not resolve this.',},
 {id:'FND-002',title:'Name variant in bank statement',label:'Observed characteristic',severity:'Medium',confidence:.78,summary:'The bank statement displays “Rahul S. Sharma”, while other documents display “Rahul Sharma”. This may be a formatting or middle-initial convention.',method:'Entity comparison · demo adapter v0.1',status:'Needs verification',evidence:[{...fields[0].anchor},{...fields[5].anchor}],alternative:'An abbreviated middle name is common and may refer to the same person.',},
 {id:'FND-003',title:'Application value aligns with extracted field',label:'Observed characteristic',severity:'Low',confidence:.89,summary:'The requested amount was read from the loan application. No independent validation of the value was performed.',method:'OCR field extraction · demo adapter v0.1',status:'Unreviewed',evidence:[{...fields[7].anchor}],alternative:'OCR may misread characters; review the original page before relying on this value.',},
 {id:'FND-004',title:'Low quality text region on page 2',label:'Observed characteristic',severity:'Informational',confidence:.64,summary:'A low-contrast area reduced text recognition confidence. The original source remains available for manual review.',method:'OCR quality heuristic · demo adapter v0.1',status:'Unreviewed',evidence:[{...fields[6].anchor}],alternative:'Scan quality or compression may explain the low confidence.',}
];
export const timeline=[{date:'2024-07-01',title:'Bank statement period begins',source:documents[2],confidence:.96},{date:'2024-10-31',title:'Salary slip issued',source:documents[1],confidence:.92},{date:'2024-11-04',title:'Loan application submitted',source:documents[3],confidence:.97},{date:'2024-11-06',title:'Address proof captured',source:documents[4],confidence:.91}];
export const auditSeed:AuditRecord[]=[{time:'Nov 06 · 10:24',actor:'A. Investigator',action:'Investigation opened',detail:'Case DF-2024-00421 · demo dataset'},{time:'Nov 06 · 10:27',actor:'System · mock',action:'Documents indexed',detail:'5 synthetic records · no backend processing'},{time:'Nov 06 · 10:28',actor:'System · mock',action:'Demo analysis loaded',detail:'4 illustrative findings; no forensic verification'}];
export const stages=['File validation & integrity identifier','Page rendering and normalization','Document classification','Text recognition (OCR)','OCR quality assessment','Information extraction','Entity resolution & relationship mapping','Adaptive cross-document analysis','Visual evidence examination','Finding aggregation & dossier preparation'];
export const standards=[['ISO/IEC 27037','Identification, collection, acquisition and preservation','Aligned design consideration','No backend acquisition controls configured'],['ISO/IEC 27041','Suitability and adequacy of investigative methods','Planned','Validation evidence not connected'],['ISO/IEC 27042','Analysis and interpretation of digital evidence','Aligned design consideration','No validated analysis pipeline'],['NIST SP 800-86','Forensic techniques integrated with incident response','Aligned design consideration','Workflow mapping only; no response handoff'],['OWASP ML Security Top 10','AI/ML security controls','Not assessed','No model security assessment available']];
