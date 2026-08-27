# Engineering rules

1. Research before building: inspect existing schemas and conventions; do not invent replacements blindly.
2. Prefer direct, simple functions over factories, frameworks, and unnecessary indirection.
3. Audit existing dependencies and helpers before adding or rewriting functionality.
4. Never replace working code with unfinished or more fragile complexity.
5. Read structure first with `rg`, then inspect only the relevant ranges.
6. Keep command and tool output focused; omit repeated or low-signal content.
7. Keep context small and spend it on behavior, constraints, and decisions.
8. Verify behavior with the relevant tests before declaring work complete.
9. Keep `packages/scheduler/checkup_scheduler` as the only production Scheduler implementation; apps and simulations import it rather than copying it.
