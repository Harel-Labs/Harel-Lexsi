"""B5 env check: AgentTune and AuditKIT import, report the available accelerator."""
import auditkit
import torch
import trl
from auditkit.agent_eval import episodes_from_agenttune  # noqa: F401  (reads trajectories.jsonl)

import agenttune

accel = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
print(f"agenttune {getattr(agenttune, '__version__', '?')} | auditkit {auditkit.__version__} | trl {trl.__version__}")
print(f"torch {torch.__version__} | accelerator: {accel}")
