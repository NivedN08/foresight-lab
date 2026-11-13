"""The research loop (see docs/swarm.md).

    from lab.swarm import run_lab
    summary = run_lab(problem, pool, compare, llm_backend="claude_cli", memory="vector",
                      search="openalex", generations=3, proposals=4)
"""
from lab.swarm.decide import Thresholds, decide, relative_improvement
from lab.swarm.digest import failure_digest
from lab.swarm.loop import CostCapReached, Lab, run_lab, source_hash

__all__ = ["CostCapReached", "Lab", "Thresholds", "decide", "failure_digest", "relative_improvement",
           "run_lab", "source_hash"]
