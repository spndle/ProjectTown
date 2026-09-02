---
name: sol-luna
description: "Deprecated ProjectTown compatibility alias for older $sol-luna prompts. Immediately use the repository's Sol-Terra workflow and Terra execution roles; never attempt to invoke a Luna model or Luna role. Prefer $sol-terra for all new requests."
---

# Deprecated compatibility alias

ProjectTown now uses Sol for control, decisions, review, and acceptance, with
Terra for bounded exploration, implementation, and verification.

When this legacy skill is invoked:

1. Do not spawn or configure a Luna role.
2. Read and follow `../sol-terra/SKILL.md` completely.
3. Tell the user that `$sol-luna` was accepted as a compatibility alias and that
   `$sol-terra` is the canonical command for future work.

This file exists only to keep older prompts safe. It contains no executable Luna
routing policy.
