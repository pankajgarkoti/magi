# Contributing

Bug reports, fixes and new agent adapters are welcome.

- **Bugs:** open an issue with the mAGI version (`rpm-ostree status` or the ISO name), the agent
  you use, and what you saw. `magi-status --once` output helps.
- **Questions and ideas:** [Discussions](https://github.com/pankajgarkoti/magi/discussions).
- **Pull requests:** keep them small, one change each. Say how you tested it (on a machine, or in
  the test VM under `tests/vm/`).
- **Agent adapters** live in `agent/lib/magi_agents.py`. An adapter says how to install, launch
  and resume the agent, how to read its idle/working state, and how to tell whether its input box
  holds a draft.
- **Browser probes** (`agent/browser-bus/examples/`) are small JS files with `@match`, `@source`
  and `@purpose` headers. Read-only probes that are useful to others are good first PRs.

Issues labelled `good first issue` are small and self-contained.

By contributing you agree your work is released under the MIT licence.
