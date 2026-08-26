"""The house style every extraction prompt shares.

Written after watching a real reader open the dashboard and bounce off it. The
findings were accurate and unreadable: "DMA and regulatory feature withholding",
"AI Infrastructure Margin Dilution", "component supply and cost inflation may
degrade hardware unit economics". Each of those is correct, and each assumes
the reader already knows the acronym, the accounting term, and why it matters.

A tool whose purpose is to save someone reading primary sources has failed if
its output needs the same background the primary sources did.
"""

PLAIN_LANGUAGE_RULES = """Write for an intelligent reader who does not work in \
finance and does not know the jargon. This is not a style preference, it is the \
point of the product: the reader came here to avoid reading the filing.

- Spell out an acronym the first time you use it, with a few words of \
explanation: "the DMA (a European law forcing app store changes)", not "DMA".
- Replace finance jargon with ordinary words. Say "profit margins" not "unit \
economics", "the market it can sell to" not "total addressable market", \
"profits are being squeezed" not "margin compression", "money coming in" not \
"revenue recognition".
- Say what actually happens to the business, in cause and effect. "Memory chips \
cost more, so each phone earns less profit" beats "component cost inflation \
pressures hardware margins".
- Never refer to a filing by its form number in a finding. The reader does not \
know what a 10-Q is. Say "the quarterly report", "the annual report", or "the \
earnings call".
- Keep every `label` a short, concrete noun phrase a non-expert would \
understand: "Rising memory chip costs" is good, "Component cost inflation \
dynamics" is not.
- No filler. Delete "it should be noted that", "potentially", "may serve to". \
If a sentence survives deletion unchanged in meaning, delete it."""
