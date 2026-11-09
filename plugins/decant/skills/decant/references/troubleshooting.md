# Troubleshooting

Read `verify.json` in the work folder. `checks` names what failed; `error` and `run_error` hold OpenCode's own message.

| Symptom | Likely cause | What to do |
|---|---|---|
| `OpenCode 2.x is required` | OpenCode 1.x or not installed | Upgrade OpenCode. The 1.x format differs and is not supported. |
| `import_accepted` false | The session format changed in this OpenCode | Compare a real export with `references/opencode-2x.md` and update `convert`. |
| `round_trip_identical` false | OpenCode rewrote or dropped a field on import | Diff `comparable()` output to find the first differing item. |
| `resumed_turn_completed` false | The fake provider could not load, or the session cannot replay | Read `run_error`. Loading the provider needs network access the first time. |
| `tool_calls_paired_with_results` false | A tool call lost its result | Check `tool_calls_unanswered` in the manifest and the source transcript. |
| `thinking_not_replayed` false | OpenCode changed which messages it replays | Do not import. A real provider may reject unsigned thinking. |
| `interface_renders_session` false | Terminal startup was slow, or a render failure | Re-run once. If it repeats, open the session in the throwaway store by hand. |
| `the session's project folder no longer exists` | The project moved or was deleted | Pass `--directory <folder>`. |
| `prefix matches several sessions` | The id prefix is too short | Use more characters, or `locate` to list them. |
| `already exists` on import | This conversion was imported before | Resume that session, or `prepare --salt <text>` for a separate copy. |
| `changed after it was verified` | `session.json` was edited | Run `prepare` again. |
| Session missing from the picker after import | OpenCode filed it under another project | Open it directly with `opencode -s <id>`, and check `project_id` in the manifest. |
| A real model rejects the first turn | A provider rule the fake model does not enforce | Report the exact error. Lowering `--tail-chars` removes older tool history from the replay. |
| The model runs out of context | The summary plus recent turns are too large | Re-run `prepare --salt <text>` with lower `--tail-chars` and `--max-summary-chars`. |
