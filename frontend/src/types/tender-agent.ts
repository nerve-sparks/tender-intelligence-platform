export interface CompanyProfile {
  id?: string;
  company_id: string;
  name: string;
  company_url: string;
  experience_years: number;
  interest_tags: string[];
  website_scrape?: {
    status: string;
    pages: string[];
    extracted_text: string;
    last_error?: string | null;
  };
  uploaded_files?: Array<{
    file_hash: string;
    original_name: string;
    local_path: string;
    mime_type?: string;
    size_bytes?: number;
    uploaded_at: string;
    extract_status?: string;
    extracted_text_len?: number;
  }>;
  status?: {
    processing_status: string;
    files_processed: number;
    total_files: number;
    last_error?: string | null;
  };
  metadata?: Record<string, unknown>;
}

export type Level = "HIGH" | "MEDIUM" | "LOW" | "NONE";
export type Recommendation = "PURSUE" | "CONSIDER" | "SKIP";

export interface EligibilityCheck {
  label: string;
  required: string;
  company: string;
  status: "met" | "not_met" | "unknown" | "exempt";
}

export interface TenderMatch {
  score: number;
  /** true once the business-relevance judgment (or a hard-gate rejection) exists */
  verified: boolean;
  procurement_type?: string | null;
  business_relevance?: Level | null;
  capability_alignment?: Level | null;
  buyer_relevance?: Level | null;
  eligibility?: {
    fit: "MET" | "NOT_MET" | "UNKNOWN";
    score: number;
    checks: EligibilityCheck[];
  };
  why_matches?: string[];
  gaps?: string[];
  recommendation?: Recommendation | null;
  /** Gemini's own call; the badge (recommendation) follows the score */
  ai_recommendation?: Recommendation | null;
  reason?: string | null;
  reasons: string[];
}

export interface MatchItem {
  tender: Tender;
  match: TenderMatch;
  action: "saved" | "applied" | "discarded" | null;
}

export interface MatchNotification {
  id: string;
  tender_id: string;
  bid_id: string;
  title?: string;
  score: number;
  recommendation?: Recommendation | null;
  end_date?: string | null;
  read: boolean;
  created_at: string;
}

export interface ScopeCategory {
  name: string;
  items: string[];
}

export interface Tender {
  id: string;
  bid_id: string;
  gem_url?: string;
  pdf_local_path?: string;
  portal?: string;
  scraped_info?: {
    department?: string;
    start_date?: string;
    end_date?: string;
    bid_type?: string;
    bid_value_range?: string;
    items?: string;
  };
  metadata?: {
    title?: string;
    department?: string;
    location?: string;
    summary?: string;
    domains?: string[];
    required_technologies?: string[];
    required_certifications?: string[];
    estimated_value?: string;
    eligibility_criteria?: Record<string, unknown>;
    // Tender brief (extraction v2)
    buyer?: string;
    sector?: string;
    procurement_type?: string;
    what_is_being_bought?: string;
    scope_items?: string[];
    scope_categories?: ScopeCategory[];
    required_products_or_services?: string[];
    min_avg_turnover?: string;
    min_experience_years?: number;
    similar_work_required?: string;
    eligibility_requirements?: string[];
    preferences?: string[];
    evaluation?: string[];
    key_dates?: {
      pre_bid_queries?: string | null;
      pre_bid_meeting?: string | null;
      bid_submission?: string | null;
      bid_opening?: string | null;
    };
    emd_amount?: string;
    contract_period?: string;
    infrastructure_notes?: string[];
  };
}

export interface DashboardStats {
  company_id?: string;
  company_name: string;
  totals: {
    tenders_analyzed: number;
    best_tenders_found: number;
    tenders_saved: number;
    tenders_applied: number;
  };
  overview_last_7_days: {
    gathering: number;
    analyzed: number;
    saved: number;
    applied: number;
  };
}

export interface DashboardReport {
  range: string;
  labels: string[];
  series: {
    gathering: number[];
    analyze: number[];
    saved: number[];
  };
}

export interface ApiErrorShape {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, string>;
  };
}
