# PortfolioPi Agent Guidelines

## Token-Saving Local Offload Policy (gemma4-local MCP)
- **Large File Pre-Filtering**: Before reading any file >150 lines into cloud context, call `gemma4-local:summarize_file` to get a compact structural map and line ranges first.
- **Log & Telemetry Triage**: Never dump raw `journalctl`, `dnsmasq`, or `tcpdump` outputs directly into cloud context. Use `gemma4-local:analyze_logs` to extract root causes and anomalous lines at 0 cloud token cost.
- **Boilerplate & First-Draft Offload**: Use `gemma4-local:ask_gemma4` or `gemma4-local:draft_code_patch` to draft initial SQL queries, regexes, unit tests, and routine code edits, reserving cloud model tokens strictly for high-level planning and final verification.
- **Hardware Pinning Rule**: `gemma4:12b` runs strictly on Mac mini (`192.168.1.18:11434`). Never run or pull `gemma4:12b` on Raspberry Pi 5 (`raspi`).
