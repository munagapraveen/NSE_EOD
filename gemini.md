# Gemini Project-Wide Instructions

- **Mandatory Planning:** Always start in **Plan Mode**. Write the plan, then read it again to ensure absolute accuracy.
- **Clarification First:** If any part of the plan is unclear or ambiguous, **STOP** and ask for clarification before writing a single line of code.
- **Manageable Scope:** If a plan is too large to fit in context or becomes unwieldy, break it into smaller, manageable pieces.
- **Iterative Design:** Engage in frequent back-and-forth communication during the planning phase. This phase is more critical than the execution.
- **setup version control:** Set up version control so you can roll back if something breaks.
- **Test it** Have the agent generate test cases you can read in plain language to confirm the code does what it's supposed to do.
- **Then Run it** Only after all of that: let auto mode run
- **Strict Adherence:** Perform ONLY the tasks explicitly requested by the user.
- **Informed Modifications:** Do NOT modify any code or the database without first informing and receiving confirmation from the user.
- **No Automatic Cloud Deployment:** Do NOT automatically deploy local code changes to the cloud VM or restart services on the VM without explicit user request/consent.
- **Ignore Todo.txt:** Do not read Todo.txt. Ignore it.
