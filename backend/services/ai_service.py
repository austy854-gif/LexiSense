import os
import json
import logging
from typing import Optional, Dict, Any
from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger(__name__)

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")

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

Return ONLY valid JSON, no additional text."""

CHAT_SYSTEM_PROMPT = """You are a helpful legal assistant specialized in contract analysis. 
Answer questions about the provided contract clearly and accurately.
If you're unsure about something, say so. Always base your answers on the contract text provided.
Be concise but thorough in your responses."""


async def analyze_contract(contract_text: str) -> Optional[Dict[str, Any]]:
    """Analyze a contract using GPT-5.2 and return structured insights."""
    if not EMERGENT_LLM_KEY:
        logger.error("EMERGENT_LLM_KEY not configured")
        return None
    
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"contract-analysis-{id(contract_text)}",
            system_message=CONTRACT_ANALYSIS_PROMPT
        ).with_model("openai", "gpt-5.2")
        
        truncated_text = contract_text[:8000] if len(contract_text) > 8000 else contract_text
        
        user_message = UserMessage(
            text=f"Analyze this contract:\n\n{truncated_text}"
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
            
            analysis = json.loads(clean_response.strip())
            return analysis
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response as JSON: {e}")
            return {
                "summary": response[:500] if response else "Analysis failed",
                "riskLevel": "unknown",
                "error": "Could not parse structured analysis"
            }
            
    except Exception as e:
        logger.error(f"AI analysis failed: {e}")
        return None


async def get_chat_response(contract_text: str, question: str, session_id: str) -> str:
    """Get an AI response to a question about a contract."""
    if not EMERGENT_LLM_KEY:
        raise ValueError("EMERGENT_LLM_KEY not configured")
    
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=session_id,
            system_message=CHAT_SYSTEM_PROMPT
        ).with_model("openai", "gpt-5.2")
        
        truncated_text = contract_text[:6000] if len(contract_text) > 6000 else contract_text
        
        user_message = UserMessage(
            text=f"Contract Text:\n{truncated_text}\n\nQuestion: {question}"
        )
        
        response = await chat.send_message(user_message)
        return response or "I couldn't generate a response. Please try again."
        
    except Exception as e:
        logger.error(f"Chat response failed: {e}")
        raise ValueError(f"Unable to process question: {str(e)}")


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
