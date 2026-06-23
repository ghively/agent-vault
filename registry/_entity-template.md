---
# ============ FRONTMATTER — PYTHON OWNS THIS. LLM MUST NOT EDIT. ============
# Required fields. Ingestion (Layer 1) writes these. Compile (Layer 2) must not touch them.
slug:                       # stable filesystem identity, lowercase-dashed, NEVER changes
type:                       # must exist in registry/schema.yaml
subtype:                    # must be a subtype of `type` in the registry
title:                      # human display name
status: stub                # stub | compiled | needs-review | unknown | archived
confidence:                 # 0.0–1.0 classifier confidence (spec §6)
created:                    # YYYY-MM-DD
sources: []                 # raw file paths this entity draws from
sources_hash:               # hash of the source set; prose recompiles only when this changes

# Optional cross-cutting attributes (spec §1.3) — include only when known:
# tags: []                  # all must exist in registry/schema.yaml tags
# aliases: []               # surface forms; mirrored into registry/aliases.yaml by promotion
# location:                 # slug ref to a property/room entity
# acquired:                 # YYYY-MM-DD
# expires:                  # YYYY-MM-DD   (the temporal hook Agent Vault queries)
# renews:                   # YYYY-MM-DD
# serviced:                 # YYYY-MM-DD
# due:                      # YYYY-MM-DD
# value:                    # number
# value_as_of:              # YYYY-MM-DD
# serial:                   # identifier (NOT a secret)
# model:                    #
# vin:                      #
# last4:                    # last 4 of an account number (NOT the full number)
# credential_ref:           # scheme://store/path — REFERENCE ONLY, never a plaintext secret
# related: []               # typed slug refs to other entities
---

<!-- ============ LINK BLOCK — PYTHON OWNS THIS. Regenerated every run. ============ -->
<!-- LINKS:BEGIN -->
<!-- LINKS:END -->

<!-- ============ PROSE BODY — LLM OWNS THIS. Python must not edit. ============ -->
<!-- A stub has an EMPTY prose body and status: stub. The compile pass fills this in
     from the linked sources and flips status to compiled. Never invent facts here —
     use [NEEDS SOURCE] markers instead. -->
