---
slug: bofa-checking
type: account
subtype: checking
title: Bank of America — Primary Checking
status: compiled
confidence: 0.97
created: 2026-05-21
sources:
  - raw/statements/bofa-checking-2026-01.pdf
  - raw/email/bofa-balance-alert-2026-02-03.eml
sources_hash: seed000002
compiled_from_hash: seed000002
tags: [banking, primary]
last4: "4417"
credential_ref: age://banking/bofa-login
related:
  - financial-institution/bank-of-america
---

<!-- LINKS:BEGIN -->
- Held at: [[financial-institution/bank-of-america]]
- Statements: [[document/bofa-statement-2026-01]]
<!-- LINKS:END -->

Primary household checking account at Bank of America, account ending 4417.
Login credentials are stored in the age secret store under banking/bofa-login
and are never recorded here in plaintext. Monthly statements are ingested and
linked above.
