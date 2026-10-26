import json
import logging
import os
import re
from typing import Optional, Dict, Any, List, Tuple
from emergentintegrations.llm.chat import LlmChat, UserMessage

from utils.contract_fields import coerce_contract_value, coerce_iso_date, coerce_risk_level
from utils.env import get_env

logger = logging.getLogger(__name__)

EMERGENT_LLM_KEY = get_env("EMERGENT_LLM_KEY")

CONTRACT_ANALYSIS_PROMPT = """You are an expert legal contract analyzer. Analyze the following contract and return a JSON object with the following structure:
{
  "summary": "A brief 2-3 sentence summary of what this contract is about",
  "parties": ["List of parties involved in the contract"],
  "keyTerms": ["List of key terms and conditions"],
  "risks": [
    {"risk": "Description of risk", "severity": "high/medium/low", "recommendation": "How to mitigate"}
  ],
  "dates": {
    "effectiveDate": "YYYY-MM-DD or null",
    "expiryDate": "YYYY-MM-DD or null",
    "renewalDate": "YYYY-MM-DD or null"
  },
  "value": "Contract value if mentioned, or null",
  "riskLevel": "high/medium/low based on overall assessment",
  "recommendations": ["List of recommendations for review"]
}

Return ONLY valid JSON, no additional text. Dates must be YYYY-MM-DD or null. If this is only an excerpt, base the JSON on that excerpt and do not invent dates or value that are not in the text."""

CHAT_SYSTEM_PROMPT = """You are a helpful legal assistant specialized in contract analysis. 
Answer questions about the provided contract clearly and accurately.
If you're unsure about something, say so. Always base your answers on the contract text provided.
Be concise but thorough in your responses.
The user message may contain excerpts rather than the full document. If the answer might depend on text you were not shown, say that explicitly."""

COVERAGE_NOTE = (
    "Only part of this contract was analyzed. Review the full document before relying on dates, value, or risk."
)

_CHUNK_CHARS = 8000
_CHUNK_OVERLAP = 400
_CHAT_BUDGET = 12000


def _max_chunks() -> int:
    try:
        return max(1, min(int(os.environ.get("AI_ANALYSIS_MAX_CHUNKS", "8")), 20))
    except (TypeError, ValueError):
        return 8


def _windows(text: str, chunk_size: int, overlap: int) -> List[Tuple[int, int]]:
    if len(text) <= chunk_size:
        return [(0, len(text))] if text else []
    step = max(1, chunk_size - overlap)
    spans: List[Tuple[int, int]] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        spans.append((start, end))
        if end >= len(text):
            break
        start += step
    return spans


def _covered_characters(spans: List[Tuple[int, int]]) -> int:
    merged: List[List[int]] = []
    for start, end in sorted(spans):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return sum(end - start for start, end in merged)


def chunk_plan(
    text: str,
    chunk_size: int = _CHUNK_CHARS,
    overlap: int = _CHUNK_OVERLAP,
    max_chunks: Optional[int] = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """Split ``text`` for analysis.

    When the document is longer than ``max_chunks``, the first chunks and the
    final chunk are kept so the signature and term section is not dropped, and
    ``coverage.partial`` is true.
    """
    text = text or ""
    limit = _max_chunks() if max_chunks is None else max(1, max_chunks)
    spans = _windows(text, chunk_size, overlap)
    partial = len(spans) > limit
    if partial:
        chosen = list(spans[: max(1, limit - 1)])
        if spans[-1] not in chosen:
            chosen.append(spans[-1])
    else:
        chosen = spans
    return [text[start:end] for start, end in chosen], {
        "totalCharacters": len(text),
        "analyzedCharacters": _covered_characters(chosen),
        "chunksAnalyzed": len(chosen),
        "partial": partial,
    }


def _parse_model_json(response: str) -> Optional[dict]:
    if not response:
        return None
    clean = response.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    elif clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    try:
        parsed = json.loads(clean.strip())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def merge_analyses(parts: List[dict]) -> Dict[str, Any]:
    """Combine per-chunk JSON. Later chunks win for dates and value."""
    summaries: List[str] = []
    parties: List[str] = []
    terms: List[str] = []
    risks: List[dict] = []
    seen_risks = set()
    recommendations: List[str] = []
    dates = {"effectiveDate": None, "expiryDate": None, "renewalDate": None}
    value = None
    best_rank = -1
    risk_level = None
    rank = {"low": 1, "medium": 2, "high": 3}

    for part in parts:
        if not isinstance(part, dict) or part.get("error"):
            continue
        summary = part.get("summary")
        if isinstance(summary, str) and summary.strip():
            summaries.append(summary.strip())
        for party in part.get("parties") or []:
            if isinstance(party, str) and party.strip() and party.strip() not in parties:
                parties.append(party.strip())
        for term in part.get("keyTerms") or []:
            if isinstance(term, str) and term.strip() and term.strip() not in terms:
                terms.append(term.strip())
        for risk in part.get("risks") or []:
            if not isinstance(risk, dict):
                continue
            label = risk.get("risk")
            key = label.strip().lower() if isinstance(label, str) else ""
            if not key or key in seen_risks:
                continue
            seen_risks.add(key)
            severity = coerce_risk_level(risk.get("severity")) or "medium"
            risks.append({
                "risk": label.strip() if isinstance(label, str) else key,
                "severity": severity,
                "recommendation": risk.get("recommendation") if isinstance(risk.get("recommendation"), str) else "",
            })
        for rec in part.get("recommendations") or []:
            if isinstance(rec, str) and rec.strip() and rec.strip() not in recommendations:
                recommendations.append(rec.strip())
        raw_dates = part.get("dates") if isinstance(part.get("dates"), dict) else {}
        for key in dates:
            coerced = coerce_iso_date(raw_dates.get(key))
            if coerced:
                dates[key] = coerced
        coerced_value = coerce_contract_value(part.get("value"))
        if coerced_value:
            value = coerced_value
        level = coerce_risk_level(part.get("riskLevel"))
        if level and rank[level] > best_rank:
            best_rank = rank[level]
            risk_level = level

    return {
        "summary": " ".join(summaries).strip()[:2000],
        "parties": parties[:20],
        "keyTerms": terms[:30],
        "risks": risks[:40],
        "dates": dates,
        "value": value,
        "riskLevel": risk_level or "unknown",
        "recommendations": recommendations[:20],
    }


def select_chat_context(text: str, question: str, budget: int = _CHAT_BUDGET) -> Tuple[str, bool]:
    """Excerpts for Q&A: the start, the end, and the windows that match the question."""
    text = text or ""
    if len(text) <= budget:
        return text, False
    head = text[:2000]
    tail = text[-2000:]
    words = [word.lower() for word in re.findall(r"[A-Za-z0-9$]{3,}", question or "")]
    middle = text[2000:-2000] if len(text) > 4000 else ""
    size = 1500
    windows = []
    for index in range(0, len(middle), size):
        piece = middle[index:index + size]
        lowered = piece.lower()
        score = sum(lowered.count(word) for word in words)
        windows.append((score, index, piece))
    windows.sort(key=lambda item: (-item[0], item[1]))
    remaining = budget - len(head) - len(tail) - 64
    chosen = []
    for score, index, piece in windows:
        if remaining <= 200:
            break
        if score == 0 and chosen:
            continue
        take = piece[:remaining]
        chosen.append((index, take))
        remaining -= len(take)
        if score == 0:
            break
    chosen.sort()
    body = "\n\n".join(piece for _, piece in chosen)
    context = f"[Beginning of contract]\n{head}\n\n[Relevant excerpts]\n{body}\n\n[End of contract]\n{tail}"
    return context, True


async def _analyze_chunk(chunk: str) -> Optional[Dict[str, Any]]:
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"contract-analysis-{id(chunk)}",
        system_message=CONTRACT_ANALYSIS_PROMPT,
    ).with_model("openai", "gpt-5.2")
    response = await chat.send_message(UserMessage(text=f"Analyze this contract:\n\n{chunk}"))
    parsed = _parse_model_json(response or "")
    if parsed is None:
        logger.error("Failed to parse AI response as JSON")
        return {
            "summary": (response or "")[:500] or "Analysis failed",
            "riskLevel": "unknown",
            "error": "Could not parse structured analysis",
        }
    return parsed


async def analyze_contract(contract_text: str) -> Optional[Dict[str, Any]]:
    """Analyze a contract and return structured insights plus coverage.

    The whole text is sent when it fits in ``AI_ANALYSIS_MAX_CHUNKS`` windows.
    Longer documents keep the opening and the closing section, and the result
    is marked ``coverage.partial`` so the UI can say the analysis is incomplete.
    """
    if not EMERGENT_LLM_KEY:
        logger.error("EMERGENT_LLM_KEY not configured")
        return None

    chunks, coverage = chunk_plan(contract_text or "")
    if not chunks:
        return None

    try:
        parts: List[dict] = []
        for chunk in chunks:
            part = await _analyze_chunk(chunk)
            if part:
                parts.append(part)
    except Exception as exc:
        logger.error("AI analysis failed: %s", type(exc).__name__)
        return None

    usable = [part for part in parts if not part.get("error")]
    if not usable:
        failed = parts[0] if parts else {
            "summary": "Analysis failed",
            "riskLevel": "unknown",
            "error": "Could not parse structured analysis",
        }
        failed = dict(failed)
        failed["coverage"] = coverage
        return failed

    merged = merge_analyses(usable)
    merged["coverage"] = coverage
    if coverage["partial"]:
        merged["coverageNote"] = COVERAGE_NOTE
    return merged


async def get_chat_response(contract_text: str, question: str, session_id: str) -> str:
    """Get an AI response to a question about a contract."""
    if not EMERGENT_LLM_KEY:
        raise ValueError("EMERGENT_LLM_KEY not configured")

    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=session_id,
            system_message=CHAT_SYSTEM_PROMPT,
        ).with_model("openai", "gpt-5.2")

        context, partial = select_chat_context(contract_text or "", question or "")
        note = ""
        if partial:
            note = (
                "\n\nThese are excerpts, not the entire contract. "
                "If the answer might depend on text you were not shown, say so."
            )
        response = await chat.send_message(
            UserMessage(text=f"Contract text:\n{context}{note}\n\nQuestion: {question}")
        )
        answer = response or "I couldn't generate a response. Please try again."
        if partial:
            answer = answer.rstrip() + "\n\nThis answer used excerpts, not the entire contract."
        return answer

    except Exception as exc:
        logger.error("Chat response failed: %s", type(exc).__name__)
        raise ValueError("Unable to process question") from exc


INTAKE_CLASSIFICATION_PROMPT = """You are a contract classification specialist. Given limited metadata about an incoming contract (email subject, sender, filename, and optionally extracted text), classify the contract and identify the counterparty.

Return ONLY valid JSON with this structure:
{
  "contract_type": "one of: NDA, Employment, Service Agreement, Lease, Partnership, Licensing, Purchase Agreement, General",
  "counterparty": "likely counterparty name or null",
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation of classification logic",
  "risk_indicators": ["list of risk indicators found in metadata"],
  "suggested_playbook": "playbook name suggestion or null"
}

Classification guidelines:
- NDA: subject/filename contains "nda", "non-disclosure", "confidentiality"
- Employment: subject/filename contains "employment", "offer", "hire", "employee"
- Service Agreement: subject/filename contains "service", "consulting", "professional services", "msa", "sow"
- Lease: subject/filename contains "lease", "rental", "rent"
- Partnership: subject/filename contains "partnership", "joint venture", "jv"
- Licensing: subject/filename contains "license", "licensing", "ip license"
- Purchase Agreement: subject/filename contains "purchase", "buy", "acquisition", "order"
- General: anything else

Extract counterparty from sender email domain, email subject, or filename.
Risk indicators: urgency language, high-value terms, regulatory keywords, exclusive terms."""


async def classify_intake_contract(
    sender_email: str,
    subject: str,
    filename: str,
    mime_type: str,
    extracted_text: str = ""
) -> Dict[str, Any]:
    """Classify an incoming contract from email intake metadata."""
    if not EMERGENT_LLM_KEY:
        logger.warning("EMERGENT_LLM_KEY not configured, using fallback classification")
        return _fallback_classification(sender_email, subject, filename)
    
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"intake-classification-{hash(sender_email + subject + filename)}",
            system_message=INTAKE_CLASSIFICATION_PROMPT
        ).with_model("openai", "gpt-5.2")
        
        # Build context from available metadata
        context_parts = [
            f"Sender: {sender_email}",
            f"Subject: {subject}",
            f"Filename: {filename}",
            f"MIME Type: {mime_type}",
        ]
        
        if extracted_text:
            # Include first 3000 chars of extracted text
            context_parts.append(f"Extracted Text (first 3000 chars):\n{extracted_text[:3000]}")
        
        user_message = UserMessage(
            text="Classify this incoming contract:\n\n" + "\n".join(context_parts)
        )
        
        response = await chat.send_message(user_message)
        
        try:
            clean_response = response.strip()
            if clean_response.startswith("```json"):
                clean_response = clean_response[7:]
            if clean_response.startswith("```"):
                clean_response = clean_response[3:]
            if clean_response.endswith("```"):
                clean_response = clean_response[:-3]
            
            classification = json.loads(clean_response.strip())
            
            # Validate required fields
            required_fields = ["contract_type", "counterparty", "confidence", "reasoning"]
            for field in required_fields:
                if field not in classification:
                    classification[field] = None
            
            # Ensure confidence is float
            classification["confidence"] = float(classification.get("confidence", 0.0))
            
            return classification
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse classification JSON: {e}")
            return _fallback_classification(sender_email, subject, filename)
            
    except Exception as e:
        logger.error(f"Intake classification failed: {e}")
        return _fallback_classification(sender_email, subject, filename)


def _fallback_classification(sender_email: str, subject: str, filename: str) -> Dict[str, Any]:
    """Fallback rule-based classification when AI is unavailable."""
    text = f"{subject} {filename}".lower()
    
    contract_type = "General"
    if any(kw in text for kw in ["nda", "non-disclosure", "confidentiality"]):
        contract_type = "NDA"
    elif any(kw in text for kw in ["employment", "offer letter", "hire", "employee"]):
        contract_type = "Employment"
    elif any(kw in text for kw in ["service agreement", "msa", "sow", "consulting", "professional services"]):
        contract_type = "Service Agreement"
    elif any(kw in text for kw in ["lease", "rental", "rent"]):
        contract_type = "Lease"
    elif any(kw in text for kw in ["partnership", "joint venture", "jv agreement"]):
        contract_type = "Partnership"
    elif any(kw in text for kw in ["license", "licensing", "ip license"]):
        contract_type = "Licensing"
    elif any(kw in text for kw in ["purchase", "acquisition", "buy", "order"]):
        contract_type = "Purchase Agreement"
    
    # Extract counterparty from sender domain
    counterparty = None
    if "@" in sender_email:
        domain = sender_email.split("@")[-1]
        # Remove common TLDs and get company name
        parts = domain.split(".")
        if len(parts) >= 2:
            counterparty = parts[-2].title()
    
    # Also check filename for company names
    if not counterparty and filename:
        # Simple extraction - look for capitalized words
        import re
        words = re.findall(r'\b[A-Z][a-z]+\b', filename)
        if words:
            counterparty = " ".join(words[:2])
    
    risk_indicators = []
    if "urgent" in text or "asap" in text or "immediate" in text:
        risk_indicators.append("urgency_language")
    if any(kw in text for kw in ["exclusive", "sole source", "single source"]):
        risk_indicators.append("exclusive_terms")
    if any(kw in text for kw in ["million", "billion", "$"]):
        risk_indicators.append("high_value")
    if any(kw in text for kw in ["gdpr", "hipaa", "pci", "sox", "regulatory", "compliance"]):
        risk_indicators.append("regulatory")
    
    return {
        "contract_type": contract_type,
        "counterparty": counterparty,
        "confidence": 0.4,
        "reasoning": "Fallback rule-based classification from metadata",
        "risk_indicators": risk_indicators,
        "suggested_playbook": f"{contract_type} Playbook" if contract_type != "General" else None
    }
