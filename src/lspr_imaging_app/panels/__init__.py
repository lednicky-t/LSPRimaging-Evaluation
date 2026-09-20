"""Display panels (sketch §7 "Display panels", §10).

All follow the same shape: read from the modules above via their query
interfaces, own no computation, own their own redraw pacing/coalescing
(§8), and turn user interaction into request calls on the owning module
rather than mutating anything themselves (AGENTS.md, "What NOT to do
without checking in again first": don't put ROI/group business logic inside
a panel/view class).
"""
