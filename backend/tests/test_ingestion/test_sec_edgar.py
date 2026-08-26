"""Regression test for the inline-XBRL text-extraction bug found during
Phase 1 manual verification: naive text extraction on a modern SEC filing
picks up the hidden `<ix:header>` XBRL metadata block ahead of the actual
readable filing text. No network access needed, this is a fixture of the
real structure SEC's iXBRL filings use.
"""

from app.ingestion.sec_edgar import SecEdgarAdapter

_IXBRL_FIXTURE = """
<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">
<head><title>test-filing</title></head>
<body>
<div style="display:none">
  <ix:header>
    <ix:hidden>
      <ix:nonNumeric name="dei:DocumentFiscalYearFocus">2026</ix:nonNumeric>
    </ix:hidden>
  </ix:header>
</div>
<div>
  <h1>UNITED STATES SECURITIES AND EXCHANGE COMMISSION</h1>
  <p>FORM 10-Q</p>
</div>
</body>
</html>
"""


def test_extract_text_strips_hidden_xbrl_header():
    text = SecEdgarAdapter._extract_text(_IXBRL_FIXTURE)

    assert "SECURITIES AND EXCHANGE COMMISSION" in text
    assert "FORM 10-Q" in text
    assert "DocumentFiscalYearFocus" not in text
    assert "2026" not in text  # only appears inside the hidden XBRL block in this fixture


def test_extract_text_handles_missing_body():
    assert SecEdgarAdapter._extract_text("<html><head></head></html>") == ""
