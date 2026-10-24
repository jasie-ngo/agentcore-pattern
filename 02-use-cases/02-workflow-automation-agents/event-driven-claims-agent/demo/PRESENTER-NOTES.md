# Presenter notes: the hard questions, and honest answers

Rule for every answer: say what was shown, what was not, and what would settle it. If you do not know, say so.

## Opening line

"I will start with a refusal, because a framework that runs whatever it is given is not worth showing leadership."

## The questions you will get

**1. "Can't someone just forge the approval?"**
Yes, today. An approval is a JSON record. The loader checks that it is bound to the exact hashes of the feature file and the vocabulary and names an approver. It does not check who wrote it. Signing, and a registry of who may approve what, is on the list of gates before any real client. The demo's approval is a labelled test fixture.

**2. "How much faster is this?"**
We do not know, and we are not claiming it. There is no baseline. The proposed experiment makes delivery effort per new client the pass or fail gate, counted against a hand-build estimate that we seal before the run. Our first client's effort figure is still to be written down.

**3. "Is the health insurer real?"**
No. We wrote five rules (six policy statements) ourselves, from public private-health-insurance rules, to show the same core running a different domain. It shows the interface works. It does not show a real insurer's rules fit. Real rules will bring tables, limits and dates, which the current policy language cannot express. The plan is plug-in rule kinds, promoted into the core only after they recur across clients.

**4. "Which insurer is this for?"**
There is no engagement with any insurer. Do not confirm or hint at one. The pack says "illustrative" because it is.

**5. "Why not use what AWS ships?"**
We use it. Cedar is the policy language AgentCore Policy uses, and Strands now evaluates Cedar in-process, which is what the demo runs. The demo does not show AgentCore Gateway or the Policy engine enforcing this pack. What we build is the part AWS does not: SME-readable rules with a controlled vocabulary, a compiler, the conformance runner, approval evidence, and domain packs. The risk that AWS absorbs more of this is real.

**6. "Does it work with a real model?"**
Not shown. The model is scripted. The check sits in the tool-call path, so a model cannot call around it, and the model is told when it is blocked. What a live model does after a refusal (retries, workarounds, a poor message to the member) is untested here.

**7. "What if an SME approves a wrong rule?"**
Then the system enforces a wrong rule, faithfully, and with evidence of who approved it. We promise evidence, not compliance. The conformance runner helps by showing what the code actually does against what was approved, which is how the drift in beat 2 was found.

**8. "The drift is one finding. Is that it?"**
On one client: 48 executable scenarios against the YAML port, one disagreement, plus 9 skipped and 12 not applicable to the older code (the newer rules don't exist there). Do not extrapolate it. It is a missing input in the ported code, not a severity rating. The point is that the rule had been written down in two places and nobody had checked them against each other.

**9. "Who would approve rules at a real client?"**
Unknown. It is the main thing the two real reviewers would tell us, which is why they are the ask. The brief's premortem ranks this first: the cost per approved rule against the value of what it unlocks.

**10. "Health data and privacy?"**
No health data is in the demo. A threat model and a data-handling statement are gates before any real health client, as is a decision on long-term member memory. The Privacy Act's automated-decision transparency duty starts 10 December 2026 per law-firm commentary. We have not checked it against the OAIC's own guidance, so quote it as commentary.

**11. "You depend on an alpha library?"**
Yes: strands-compose is alpha with a single maintainer, and the repo does not lock it. The plan is to pin and lock it behind the factory seam and write down the triggers for replacing it. The trigger values are not set. This demo is locked with hashes, but it does not use that library.

**12. "Why Gherkin?"**
SMEs can read it, and the words are controlled: each phrase means exactly what the vocabulary says, and an unknown tool, argument or value stops the build. That is also the weakness. Someone has to write and approve the vocabulary, and that is the cost the experiment measures.

## What the demo does not show

Deployment, AgentCore Gateway or Policy enforcement, a live model, the factory wiring (it is covered by tests that need the full dependency set, not by this demo), signed approvals, memory or identity, routing-gate rules for the second domain, any real SME review.

## If something breaks on the day

Use `demo/TRANSCRIPT.txt`. Say it is a recorded run of the same script. Do not improvise a live fix in front of the audience.
