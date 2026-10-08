# Frozen evaluation sets for the conversation engine (2026-10-09)

These files are frozen and must not be edited. A change to any of them invalidates the comparison between the rules engine and the model engine. If a file has to change, start a new set with a new date, and keep this one.

| File | sha256 | Content |
|---|---|---|
| `regression/conversations.yaml` | `d76c957447b9eedb1439a9445af6ca87f4efb0773da06c8566f30a9eb8a24a2f` | Byte copy of `evaluation/real/conversations.yaml` at develop `a478f219`: 23 real conversations, 135 turns. |
| `regression/ux.yaml` | `e94f18984766868b694f7179163ed26c296b34c42a3bdebb3527b072fdb1e92a` | Byte copy of `evaluation/ux/conversations.yaml`: 10 episodes, 60 turns. |
| `generalization/conversations.yaml` | `ea97364efbacabe54eceb761ac9909767dc3f59fd0f34ba647fedc57198f71af` | 33 paraphrased variants plus 23 new combination conversations: 56 conversations, 344 turns. |

## What each set measures

**Regression.** These are the conversations the rules engine was tuned on, round after round. Both engines are measured on them and the results are reported, but **they are not the decision**: the rules engine has effectively seen them.

**Generalization.** An agent wrote these on 2026-10-09 without ever reading `src/`, `tests/`, `scripts/` or any run report. Its only inputs were:
- the regression conversations;
- the rubric (`.claude/skills/finguard-real-conversation/SKILL.md`);
- a description of the holder's data.

It contains:
- (a) one variant of every regression conversation and ux episode, with the same goal, the same number of turns and the same intent, but every `say` reworded;
- (b) 23 new conversations that combine three or more mechanisms, in orders the regression set never uses.

Every `want` describes the intended behaviour, not any engine's.

## Rules
- Nobody implementing either engine opens `generalization/`. Nothing is tuned on it, and none of its turns may become a test or an example in a prompt.
- Both engines run on the same seeded data, the same bank and the same rubric. The judge sees runs labelled A and B, not the engine names.
- The decision criteria are stated in `docs/superpowers/specs/2026-10-09-llm-conversation-engine-design.md` before any run.
