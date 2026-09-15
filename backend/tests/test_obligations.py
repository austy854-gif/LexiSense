import pytest
from services.obligation_tasks import extract_obligations_from_text, OBLIGATION_PATTERNS

def test_extract_payment_obligation():
    """Test extraction of payment obligations with amount and due date."""
    text = "Client shall pay $10,000 within 30 days of invoice."
    obligations = extract_obligations_from_text(text, "contract-1", "org-1")
    payment_obls = [o for o in obligations if o['obligation_type'] == 'payment']
    assert len(payment_obls) > 0
    assert payment_obls[0]['amount'] == 10000.0
    assert payment_obls[0]['currency'] == 'USD'
    assert payment_obls[0]['due_date'] is not None
    # Due date should be ~30 days from now
    due = datetime.fromisoformat(payment_obls[0]['due_date'])
    expected = datetime.now(timezone.utc).date() + timedelta(days=30)
    assert due == expected

def test_extract_payment_obligation_eur():
    """Test extraction of payment with EUR currency."""
    text = "Customer shall pay EUR 5,000 within 14 days."
    obligations = extract_obligations_from_text(text, "contract-1", "org-1")
    payment_obls = [o for o in obligations if o['obligation_type'] == 'payment']
    assert len(payment_obls) > 0
    assert payment_obls[0]['amount'] == 5000.0
    assert payment_obls[0]['currency'] == 'EUR'

def test_extract_reporting_obligation():
    """Test extraction of reporting obligations with frequency."""
    text = "Vendor shall provide SOC 2 report annually."
    obligations = extract_obligations_from_text(text, "contract-1", "org-1")
    report_obls = [o for o in obligations if o['obligation_type'] == 'reporting']
    assert len(report_obls) > 0
    assert report_obls[0]['frequency'] == 'annually'

def test_extract_reporting_quarterly():
    """Test extraction of quarterly reporting."""
    text = "Provider shall submit quarterly status reports."
    obligations = extract_obligations_from_text(text, "contract-1", "org-1")
    report_obls = [o for o in obligations if o['obligation_type'] == 'reporting']
    assert len(report_obls) > 0
    assert report_obls[0]['frequency'] == 'quarterly'

def test_extract_delivery_obligation():
    """Test extraction of delivery obligations."""
    text = "Supplier shall deliver the goods by 15 days after order confirmation."
    obligations = extract_obligations_from_text(text, "contract-1", "org-1")
    delivery_obls = [o for o in obligations if o['obligation_type'] == 'delivery']
    assert len(delivery_obls) > 0
    assert delivery_obls[0]['due_date'] is not None

def test_extract_compliance_obligation():
    """Test extraction of compliance obligations."""
    text = "Both parties shall comply with all applicable laws and regulations."
    obligations = extract_obligations_from_text(text, "contract-1", "org-1")
    compliance_obls = [o for o in obligations if o['obligation_type'] == 'compliance']
    assert len(compliance_obls) > 0

def test_extract_renewal_obligation():
    """Test extraction of renewal notice obligations."""
    text = "Either party must provide 60 days notice prior to renewal."
    obligations = extract_obligations_from_text(text, "contract-1", "org-1")
    renewal_obls = [o for o in obligations if o['obligation_type'] == 'renewal']
    assert len(renewal_obls) > 0
    assert renewal_obls[0]['due_date'] is not None

def test_extract_termination_obligation():
    """Test extraction of termination notice obligations."""
    text = "This agreement may be terminated with 30 days written notice."
    obligations = extract_obligations_from_text(text, "contract-1", "org-1")
    term_obls = [o for o in obligations if o['obligation_type'] == 'termination']
    assert len(term_obls) > 0

def test_extract_confidentiality_obligation():
    """Test extraction of confidentiality survival obligations."""
    text = "Confidentiality obligations shall survive for 2 years after termination."
    obligations = extract_obligations_from_text(text, "contract-1", "org-1")
    conf_obls = [o for o in obligations if o['obligation_type'] == 'confidentiality']
    assert len(conf_obls) > 0
    # Should detect the 2 year survival period

def test_obligated_party_detection_our_party():
    """Test detection of 'our party' as obligated."""
    text = "We shall provide the services within 30 days."
    obligations = extract_obligations_from_text(text, "contract-1", "org-1")
    delivery_obls = [o for o in obligations if o['obligation_type'] == 'delivery']
    assert len(delivery_obls) > 0
    assert delivery_obls[0]['obligated_party'] == 'our_party'

def test_obligated_party_detection_counterparty():
    """Test detection of counterparty as obligated."""
    text = "You shall pay the fees within 30 days."
    obligations = extract_obligations_from_text(text, "contract-1", "org-1")
    payment_obls = [o for o in obligations if o['obligation_type'] == 'payment']
    assert len(payment_obls) > 0
    assert payment_obls[0]['obligated_party'] == 'counterparty'

def test_obligation_structure_completeness():
    """Test that extracted obligations have all required fields."""
    text = "Vendor shall pay $5,000 within 10 days."
    obligations = extract_obligations_from_text(text, "contract-1", "org-1")
    assert len(obligations) > 0
    obl = obligations[0]
    required_fields = [
        'contractId', 'organizationId', 'obligation_type', 'title',
        'description', 'obligated_party', 'beneficiary_party',
        'due_date', 'frequency', 'status', 'extraction_confidence',
        'extraction_model', 'extraction_version', 'evidence_required'
    ]
    for field in required_fields:
        assert field in obl, f"Missing field: {field}"

def test_multiple_obligations_same_text():
    """Test extraction of multiple obligation types from same text."""
    text = """
    Vendor shall pay $10,000 within 30 days.
    Vendor shall provide SOC 2 report annually.
    Client shall provide data within 7 days.
    """
    obligations = extract_obligations_from_text(text, "contract-1", "org-1")
    types = {o['obligation_type'] for o in obligations}
    assert 'payment' in types
    assert 'reporting' in types
    assert 'delivery' in types or 'compliance' in types

# Need imports
from datetime import datetime, timezone, timedelta