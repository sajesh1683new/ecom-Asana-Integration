# Implementation decisions

- The previous prototype is preserved in ignored `legacy-prototype/`; this is a fresh application and database.
- Roles are enforced labels, not authentication. The selected role travels in `X-Role`.
- AI runs before targeted human review. Entering human review follows AI completion; leaving it requires all automated findings to be triaged. The example error in the brief about entering human review is interpreted as a leaving-review guard, to avoid a circular dependency.
- F4 never gains a location silently: a human must explicitly map the imported comment to the measured feature text and viewport. Rule findings can independently identify that text before this clarification.
- Screenshots establish visual context; DOM measurements provide explainable values. Neither makes the human defect or release decision.
- Functional checks, actual devices, intended behaviour, content approval, defect acceptance and release approval remain human responsibilities.
