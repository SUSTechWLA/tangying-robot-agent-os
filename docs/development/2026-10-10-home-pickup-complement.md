# Household pickup verb complement

The original `focus-grasp-001` request included “从厨房出发，拿起杯子放进收纳盘，
然后返回客厅”. Its GOAL model submitted that complete household operation twice,
but the legacy intent parser recognized only 拿 directly followed by an object.
It left 起 unconsumed and correctly refused to execute a partial navigation-only
task. The model's fallback did not return JSON intent, and planning ended at 422
without creating or approving a task. The original attempt is retained as
`planning-attempt-2957f4d7f835db8461c85b95f0e058cb`, including nine actual model
input envelopes. Its later explanation about a missing tray map annotation was
a model inference, not independently verified diagnosis of the parser failure.

The lexical change allows one optional 起 immediately after 拿, 取, 抓 or 拾 in
the two existing household object patterns. It does not allow 起 after 把, 将 or
an object-list conjunction, accept general intervening text, or replace/remove
parts of the request. Existing capture groups still carry the same colour and
object category. The full route, operation room index, object list, destination
colour/side/inside relation, and complete-clause checks remain unchanged.

The exact subrequest now yields one `home_manipulation` intent for an unspecified
colour cup and storage bin, with route `kitchen → living_room` and manipulation
route index zero. It does not invent coordinates, bypass grounding, or certify
that the physical object is visible or reachable. Unsupported names, incomplete
container instructions, unknown operations, negative/conditional requests,
ambiguous objects and unresolved relationships continue to require clarification.

Regression coverage compares all four verb forms with their previous direct-verb
equivalents across complete, split-clause, coloured, enumerated and sequential
transfers. Rejection cases ensure the change cannot erase an unknown suffix,
appliance operation, unsupported room, relation or pending object. A real HTTP
model fixture receives the exact full focused request, proposes the original
three-capability sequence, and creates only an unapproved LLM-sourced plan with
zero provider invocations. The GOAL model route, proposal/read limits, authority
checks and existing long-horizon sentence are unchanged.

This grammar regression is not a physical grasp result. A new run using the same
natural-language request must still pass pinned observation checks, same-object
grasp and placement verification, and the return route before the focused task
is considered successful. The prior failed evidence is immutable.
