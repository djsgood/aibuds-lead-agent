"""Deterministic source-linked operational signals; no invented causes or pain."""
import re
from datetime import datetime
from lead_agent.pipeline_utils import public_url

SIGNALS = {
 'slow_response':r'\b(?:never (?:called|responded)|no (?:call ?back|response)|slow to respond|did not respond)\b',
 'follow_up':r'\b(?:no follow.up|never followed up|did not follow up)\b',
 'scheduling':r'\b(?:missed appointment|scheduling problem|appointment was canceled|appointment was cancelled)\b',
 'manual_intake':r'\b(?:fill out (?:the |a )?form|request a quote|schedule service)\b',
}
SOLUTIONS = {'slow_response':'Customer inquiry triage and response routing',
 'follow_up':'Tracked customer follow-up reminders', 'scheduling':'Appointment reminders and scheduling coordination',
 'manual_intake':'Structured intake and CRM routing'}


def analyze_documents(documents):
    findings=[]
    for doc in documents:
        url=public_url(doc['source_url'])
        text=doc['text']
        if not isinstance(text,str) or len(text)>500000: raise ValueError('Invalid document text')
        if datetime.fromisoformat(doc['retrieved_at']).tzinfo is None: raise ValueError('Timestamp requires timezone')
        kind=doc.get('kind','business_website')
        if kind not in ('business_website','customer_review','company_announcement','professional_post'):
            raise ValueError('Unsupported evidence category')
        for signal,pattern in SIGNALS.items():
            match=re.search(pattern,text,re.I)
            if not match: continue
            quote=text[max(0,match.start()-60):match.end()+100]
            complaint=kind=='customer_review' and signal!='manual_intake'
            findings.append({'signal':signal,'source_url':url,'quote':quote,
                'retrieved_at':doc['retrieved_at'], 'classification':'direct_customer_complaint' if complaint else 'observable_indicator',
                'confidence':'source_attributed_unconfirmed', 'reason':'The source explicitly contains the quoted statement; underlying cause is unknown.',
                'hypothesis':SOLUTIONS[signal]+' may help if the reported workflow is confirmed.',
                'problem_verified':False, 'cause':'unknown'})
    return findings
