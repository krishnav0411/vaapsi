"""D2 acceptance demo: the full stage-4 loop, offline and deterministic.

Builds a throwaway database in a temp dir, feeds in 10 synthetic halted
episodes (6 treatment, 4 control), and runs the action layer over them.
Expected outcome is printed at the end so the demo either passes cleanly
or exits nonzero.
"""
