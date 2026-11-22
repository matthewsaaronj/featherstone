"""Core Featherstone scripture-memorization logic, independent of any UI.

Modules:
    refs      – parse, normalize, format and compare scripture references
    texts     – fetch clean verse text from nephi.org / bible-api.com
    hints     – hide words for recall practice (ported from the Reminders agent)
    schedule  – fixed-date Featherstone cadence (daily → weekly → monthly → mastered)
    settings  – default settings (same shape as the agent's config.json)
    ai        – optional ChatGPT verse suggestion by theme
"""
