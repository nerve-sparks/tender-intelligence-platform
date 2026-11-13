"""
LLM-based metadata extraction using a configurable provider.
Supports full document processing with intelligent chunking for large documents.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


TENDER_EXTRACTION_VERSION = 2

TENDER_PROMPT = """
You are extracting a structured brief from a government tender document.
Focus on WHAT IS ACTUALLY BEING PURCHASED and what the bidder must deliver, not on boilerplate terms and conditions.
Return ONLY valid JSON with the following keys (use null or [] when not stated):
- title: short descriptive title of the requirement
- department: buying organisation / department
- buyer: ministry or organisation name (e.g. "Indian Navy", "Ministry of Health")
- sector: buyer sector (e.g. defence, health, education, railways, power)
- procurement_type: exactly one of "goods", "services", "works", "software", "consulting", "mixed"
    goods = supply of physical items; works = construction / installation / civil works;
    services = manpower or operational services; software = building, licensing or deploying software / IT / AI systems;
    consulting = advisory / studies; mixed = substantial combination of the above
- what_is_being_bought: one plain-English sentence describing the deliverable
- scope_items: array of short bullet strings describing the scope of work
- scope_categories: array of objects {{"name": string, "items": [string]}} when the tender splits scope into categories / lots / parts, else []
- required_products_or_services: array of the concrete products or services the supplier must deliver
- domains (array of strings)
- required_technologies (array of strings)
- required_certifications (array of strings)
- min_avg_turnover: minimum average annual turnover required, as written (e.g. "₹4.93 Crore") or null
- min_experience_years: number or null
- similar_work_required: description of past/similar work experience required, or null
- eligibility_requirements: array of other mandatory bidder qualifications
- preferences: array (e.g. "MSE purchase preference", "Make in India (MII) compliance", "Startup exemption")
- evaluation: array describing the evaluation method (e.g. "Two-packet bid", "Mandatory Proof of Concept", "Reverse Auction", "L1", "QCBS")
- key_dates: object with keys pre_bid_queries, pre_bid_meeting, bid_submission, bid_opening (strings as written, or null)
- emd_amount: as written (e.g. "₹1,84,93,108") or null
- estimated_value: as written or null
- contract_period: as written or null
- infrastructure_notes: array of notable delivery / infrastructure constraints (e.g. "Air-gapped, on-premise", "Buyer-owned GPU cluster")
- location
- delivery_period
- eligibility_criteria (object with keys: min_turnover, min_experience_years, required_registrations)
- required_experience_years (number or null)
- summary: 2-4 sentence summary of the requirement

Text:
{content}
"""


COMPANY_PROMPT = """
You are extracting a structured offering profile from a company's documents. This profile will be used to decide
which government tenders the company can realistically bid for, so be concrete about what the company SELLS and DELIVERS.
Return ONLY valid JSON with the following keys:
- company_name
- business_type: one short phrase (e.g. "AI services and software", "Civil construction contractor", "Medical equipment supplier")
- core_capabilities: array of the specific things the company delivers (e.g. "LLM fine-tuning", "road construction", "CCTV installation")
- offerings: array of named products / services the company sells
- industries (array of strings)
- capabilities (array of strings)
- certifications (array of strings)
- technologies (array of strings)
- domains (array of strings)
- target_sectors: array of customer sectors served (e.g. "defence", "healthcare", "manufacturing")
- preferred_procurement_types: array using only "goods", "services", "works", "software", "consulting" - the kinds of contracts this company can fulfil
- excluded_capabilities: array of tender requirement types this company clearly CANNOT fulfil, inferred from its business
    (e.g. an AI software firm cannot do "civil construction", "pharmaceutical supply", "hardware manufacturing";
     a construction firm cannot do "software development"). Use short phrases of 1-3 words.
- past_clients (array of strings)
- government_experience (boolean or null)
- years_in_business (number or null)
- employee_count (string or null)
- annual_turnover (string as written, or null)
- annual_turnover_inr (number in Indian rupees, or null)
- locations (array of strings)
- registrations (array of strings, e.g. "MSME", "Startup India", "GeM seller")
- is_dpiit_startup: true if the company is a DPIIT-recognised / Startup India registered startup, else false or null
- is_mse: true if the company is a registered Micro or Small Enterprise (MSME / Udyam), else false or null
- tender_search_keywords: array of 6-10 short search terms (1-3 words each) that would appear in the TITLES or ITEM
    CATEGORY names of Indian government GeM bids for work this company can do (e.g. "artificial intelligence",
    "software development", "data analytics", "chatbot"; or for a builder "road construction", "boundary wall").
    Use plain procurement wording, not marketing language.
- summary

Text:
{content}
"""


SEARCH_KEYWORDS_PROMPT = """
Below is a company's offering profile. List 6-10 short search terms (1-3 words each) that would appear in the
TITLES or ITEM CATEGORY names of Indian government GeM (Government e-Marketplace) bids for work this company can
actually deliver. Use plain procurement wording as a government buyer would write it, not marketing language.
Do not include terms for things the company does not do.

Company profile (JSON):
{company}

Return ONLY valid JSON: {{"keywords": ["...", "..."]}}
"""


RELEVANCE_PROMPT = """
You are a bid/no-bid analyst. Decide whether the company below can realistically bid for and deliver the tender below.

Rules:
- Judge on WHAT IS BEING PURCHASED and what the supplier must deliver, not on shared words.
  Shared generic words (software, technology, government, healthcare, security, infrastructure) are NOT evidence of relevance.
- If the tender is mainly supply of physical goods, construction, or installation work, it is only relevant if the company
  itself supplies those goods or does that work.
- A buyer's identity (e.g. defence, health) is neither positive nor negative on its own; judge the actual scope.
- If the company profile lists the requirement under excluded capabilities, business_relevance must be "NONE".
- If the company profile has past_decisions, treat "pursued" tenders as examples of what this company wants and
  "rejected" tenders as examples of what it does not want; rate similar tenders accordingly.

Company profile (JSON):
{company}

Tender brief (JSON):
{tender}

Return ONLY valid JSON with these keys:
- business_relevance: "HIGH" | "MEDIUM" | "LOW" | "NONE"  (is this within the company's business?)
- capability_alignment: "HIGH" | "MEDIUM" | "LOW" | "NONE"  (can the company deliver the stated scope?)
- buyer_relevance: "HIGH" | "MEDIUM" | "LOW" | "NONE"  (is this buyer / sector addressable for the company?)
- why_matches: array of up to 4 short concrete reasons (empty if not relevant)
- gaps: array of up to 4 short concrete gaps or risks
- recommendation: "PURSUE" | "CONSIDER" | "SKIP"
- reason: one sentence explaining the decision
"""


CHUNK_MERGE_PROMPT = """
You are merging metadata extracted from multiple parts of a document.
Combine the following metadata objects into a single comprehensive object.
For arrays: combine and deduplicate items.
For strings: pick the most detailed/complete value.
For numbers: pick the most specific value.
For booleans: if any is true, result is true.
Return ONLY valid JSON.

Metadata objects to merge:
{metadata_list}
"""


class LLMExtractor:
    def __init__(self) -> None:
        self.provider = settings.LLM_PROVIDER.lower()
        self._model = None
        self._client: Optional[httpx.Client] = None

        if self.provider == "gemini":
            if not settings.GEMINI_API_KEY:
                raise ValueError("GEMINI_API_KEY is not configured")
            import google.generativeai as genai

            genai.configure(api_key=settings.GEMINI_API_KEY)
            self._model = genai.GenerativeModel(settings.GEMINI_MODEL)
        elif self.provider in {"ollama", "local"}:
            self._client = httpx.Client(timeout=settings.LLM_TIMEOUT_SECONDS)
        else:
            raise ValueError(f"Unsupported LLM_PROVIDER: {settings.LLM_PROVIDER}")

    def _get_max_chars(self) -> int:
        """Get maximum characters based on provider."""
        if self.provider == "gemini":
            return settings.LLM_GEMINI_MAX_CHARS
        return settings.LLM_INPUT_MAX_CHARS

    def _split_into_chunks(self, text: str) -> List[str]:
        """Split text into overlapping chunks for processing."""
        max_chars = self._get_max_chars()

        # If text fits within limit, return as single chunk
        if len(text) <= max_chars:
            return [text]

        chunk_size = settings.LLM_CHUNK_SIZE
        overlap = settings.LLM_CHUNK_OVERLAP
        chunks = []
        start = 0

        while start < len(text):
            end = start + chunk_size

            # Try to break at a sentence or paragraph boundary
            if end < len(text):
                # Look for paragraph break
                para_break = text.rfind('\n\n', start + chunk_size - overlap, end)
                if para_break > start:
                    end = para_break
                else:
                    # Look for sentence break
                    sentence_break = text.rfind('. ', start + chunk_size - overlap, end)
                    if sentence_break > start:
                        end = sentence_break + 1

            chunks.append(text[start:end].strip())
            start = end - overlap  # Overlap for context continuity

            # Prevent infinite loop
            if start >= len(text) - overlap:
                break

        logger.info("llm.text_chunked", total_length=len(text), num_chunks=len(chunks))
        return chunks

    def _merge_metadata(self, metadata_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge multiple metadata dicts into one comprehensive result."""
        if not metadata_list:
            return {}
        if len(metadata_list) == 1:
            return metadata_list[0]

        # Try LLM-based merging for better results
        try:
            metadata_json = json.dumps(metadata_list, indent=2)
            prompt = CHUNK_MERGE_PROMPT.format(metadata_list=metadata_json)
            response_text = self._generate(prompt)
            merged = self._parse_json(response_text)
            if merged:
                return merged
        except Exception as exc:
            logger.info("llm.merge_failed", error=str(exc))

        # Fallback: manual merging
        return self._manual_merge(metadata_list)

    def _manual_merge(self, metadata_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Manually merge metadata when LLM merging fails."""
        merged: Dict[str, Any] = {}

        for metadata in metadata_list:
            for key, value in metadata.items():
                if value is None:
                    continue

                existing = merged.get(key)

                if existing is None:
                    merged[key] = value
                elif isinstance(value, list) and isinstance(existing, list):
                    # Merge and deduplicate arrays
                    combined = existing + [v for v in value if v not in existing]
                    merged[key] = combined
                elif isinstance(value, str) and isinstance(existing, str):
                    # Keep longer/more detailed string
                    if len(value) > len(existing):
                        merged[key] = value
                elif isinstance(value, bool):
                    # If any is True, result is True
                    merged[key] = existing or value
                elif isinstance(value, (int, float)) and existing is None:
                    merged[key] = value

        return merged

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8))
    def extract_tender(self, text: str) -> Dict[str, Any]:
        """Extract tender metadata from full document text."""
        max_chars = self._get_max_chars()

        # If text fits within limit, process directly
        if len(text) <= max_chars:
            prompt = TENDER_PROMPT.format(content=text)
            response_text = self._generate(prompt)
            return self._parse_json(response_text)

        # For very large documents, use chunking
        chunks = self._split_into_chunks(text)
        metadata_list = []

        for i, chunk in enumerate(chunks):
            logger.info("llm.processing_chunk", chunk_num=i+1, total_chunks=len(chunks))
            prompt = TENDER_PROMPT.format(content=chunk)
            response_text = self._generate(prompt)
            metadata = self._parse_json(response_text)
            if metadata:
                metadata_list.append(metadata)

        return self._merge_metadata(metadata_list)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8))
    def extract_company(self, text: str) -> Dict[str, Any]:
        """Extract company metadata from full document text."""
        max_chars = self._get_max_chars()

        # If text fits within limit, process directly
        if len(text) <= max_chars:
            prompt = COMPANY_PROMPT.format(content=text)
            response_text = self._generate(prompt)
            return self._parse_json(response_text)

        # For very large documents, use chunking
        chunks = self._split_into_chunks(text)
        metadata_list = []

        for i, chunk in enumerate(chunks):
            logger.info("llm.processing_chunk", chunk_num=i+1, total_chunks=len(chunks))
            prompt = COMPANY_PROMPT.format(content=chunk)
            response_text = self._generate(prompt)
            metadata = self._parse_json(response_text)
            if metadata:
                metadata_list.append(metadata)

        return self._merge_metadata(metadata_list)

    def _generate(self, prompt: str) -> str:
        if self.provider == "gemini":
            response = self._model.generate_content(prompt)
            return response.text

        payload = {"model": settings.LLM_MODEL, "prompt": prompt, "stream": False}
        url = settings.LLM_BASE_URL.rstrip("/") + "/api/generate"
        headers = {"Content-Type": "text/plain"}
        response = self._client.post(url, content=json.dumps(payload), headers=headers)
        response.raise_for_status()
        try:
            data = response.json()
            return data.get("response") or data.get("text") or response.text
        except json.JSONDecodeError:
            return response.text

    def _parse_json(self, text: str) -> Dict[str, Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                logger.info("llm.json_parse_failed", raw=text[:300])
                return {}
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                logger.info("llm.json_parse_failed", raw=text[:300])
                return {}

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=8))
    def judge_relevance(self, company: Dict[str, Any], tender: Dict[str, Any]) -> Dict[str, Any]:
        """Ask the LLM for a business-relevance judgment of one tender for one company."""
        prompt = RELEVANCE_PROMPT.format(
            company=json.dumps(company, ensure_ascii=False, default=str)[:6000],
            tender=json.dumps(tender, ensure_ascii=False, default=str)[:8000],
        )
        result = self._parse_json(self._generate(prompt))
        if not result or "business_relevance" not in result:
            raise ValueError("LLM relevance judgment returned no usable JSON")
        return result

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=8))
    def suggest_search_keywords(self, company: Dict[str, Any]) -> List[str]:
        """GeM search terms for a company profile that was extracted without them."""
        prompt = SEARCH_KEYWORDS_PROMPT.format(company=json.dumps(company, ensure_ascii=False, default=str)[:6000])
        result = self._parse_json(self._generate(prompt))
        keywords = result.get("keywords") if isinstance(result, dict) else None
        if not isinstance(keywords, list):
            raise ValueError("LLM returned no keywords")
        return [str(k).strip() for k in keywords if str(k).strip()]

    def generate_text(self, prompt: str) -> str:
        """Generate free-form text using the configured provider."""
        return self._generate(prompt)
