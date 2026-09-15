import pytest
from services.playbook_tasks import extract_clauses, match_clause_to_rule

def test_extract_clauses_finds_limitation_of_liability():
    """Test extraction of limitation of liability clauses."""
    text = "LIMITATION OF LIABILITY: Vendor's liability is capped at fees paid."
    clauses = extract_clauses(text)
    assert "limitation_of_liability" in clauses
    assert len(clauses["limitation_of_liability"]) > 0
    assert "capped at fees paid" in clauses["limitation_of_liability"][0].lower()

def test_extract_clauses_finds_indemnification():
    """Test extraction of indemnification clauses."""
    text = "INDEMNIFICATION: Each party shall indemnify the other against claims."
    clauses = extract_clauses(text)
    assert "indemnification" in clauses
    assert len(clauses["indemnification"]) > 0

def test_extract_clauses_finds_termination():
    """Test extraction of termination clauses."""
    text = "TERMINATION: Either party may terminate with 30 days written notice."
    clauses = extract_clauses(text)
    assert "termination" in clauses
    assert len(clauses["termination"]) > 0

def test_extract_clauses_finds_confidentiality():
    """Test extraction of confidentiality clauses."""
    text = "CONFIDENTIALITY: Both parties shall protect confidential information."
    clauses = extract_clauses(text)
    assert "confidentiality" in clauses
    assert len(clauses["confidentiality"]) > 0

def test_extract_clauses_finds_multiple_occurrences():
    """Test extraction finds multiple occurrences of same clause type."""
    text = """
    LIMITATION OF LIABILITY: Vendor's liability is capped at fees paid.
    Some other text here.
    LIMITATION OF LIABILITY: In no event shall vendor exceed $10,000.
    """
    clauses = extract_clauses(text)
    assert "limitation_of_liability" in clauses
    assert len(clauses["limitation_of_liability"]) == 2

def test_match_preferred_language_rule():
    """Test matching preferred language rule."""
    rule = {
        "rule_type": "preferred_language",
        "clause_name": "limitation_of_liability",
        "preferred_text": "liability shall not exceed fees paid"
    }
    clause = "Vendor's total liability is unlimited."
    result = match_clause_to_rule(clause, rule)
    assert result is not None
    assert result["action"] == "replace"
    assert result["suggested_text"] == "liability shall not exceed fees paid"

def test_match_fallback_language_rule():
    """Test matching fallback language rule."""
    rule = {
        "rule_type": "fallback_language",
        "clause_name": "limitation_of_liability",
        "fallback_texts": [
            "liability capped at fees paid",
            "liability limited to direct damages"
        ]
    }
    clause = "Vendor accepts unlimited liability."
    result = match_clause_to_rule(clause, rule)
    assert result is not None
    assert result["action"] == "replace"
    assert result["suggested_text"] == "liability capped at fees paid"

def test_match_must_have_missing_clause():
    """Test must_have rule when clause is missing."""
    rule = {
        "rule_type": "must_have",
        "clause_name": "limitation_of_liability",
        "preferred_text": "Standard limitation clause"
    }
    # Clause not present - handled at analysis level, not match level
    clause = "Some unrelated text about payment terms."
    result = match_clause_to_rule(clause, rule)
    # Should not match since clause_name not in text
    assert result is None

def test_match_must_not_have_rule():
    """Test must_not_have rule flags unwanted clause."""
    rule = {
        "rule_type": "must_not_have",
        "clause_name": "unlimited_liability"
    }
    clause = "UNLIMITED LIABILITY: Vendor accepts unlimited liability."
    result = match_clause_to_rule(clause, rule)
    assert result is not None
    assert result["action"] == "remove"

def test_match_negotiation_boundary_min():
    """Test negotiation boundary with minimum value."""
    rule = {
        "rule_type": "negotiation_boundary",
        "clause_name": "payment_terms",
        "min_value": 30,
        "unit": "days"
    }
    clause = "Payment due within 15 days of invoice."
    result = match_clause_to_rule(clause, rule)
    assert result is not None
    assert result["action"] == "modify"
    assert "below minimum" in result["message"]

def test_match_negotiation_boundary_max():
    """Test negotiation boundary with maximum value."""
    rule = {
        "rule_type": "negotiation_boundary",
        "clause_name": "liability_cap",
        "max_value": 100000,
        "unit": "USD"
    }
    clause = "Liability capped at $500,000."
    result = match_clause_to_rule(clause, rule)
    assert result is not None
    assert result["action"] == "modify"
    assert "exceeds maximum" in result["message"]

def test_match_conditional_rule():
    """Test conditional rule matching."""
    rule = {
        "rule_type": "conditional",
        "clause_name": "termination",
        "condition": "termination for convenience",
        "then_rule_id": "rule-123"
    }
    clause = "Either party may terminate for convenience with 30 days notice."
    result = match_clause_to_rule(clause, rule)
    assert result is not None
    assert result["action"] == "conditional"
    assert result["then_rule_id"] == "rule-123"