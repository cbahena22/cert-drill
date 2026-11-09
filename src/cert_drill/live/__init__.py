"""Optional real-model path (writer, blind pass, grader). Nothing here runs unless the learner asks for it with `--live --go`.

The `anthropic` package (the `live` extra) is imported only when a real client is made; mock and replay modes never make one.
Every reply is data for the plain-code checks: the checker decides which questions pass, code decides every grade.
"""
from .adapters import (blind_request, estimate_write, grader_request, live_grader, parse_blind, parse_grader, parse_writer, plan_write,
                       run_write, sources_block, writer_request)
from .core import (BLIND_MODEL, GRADER_MODEL, HARD_CAP_USD, PRICES, WRITER_MODEL, Budget, BudgetExceeded, Caller, LiveError, Recording,
                   actual_usd, estimate_usd, make_client, request_hash)

__all__ = ["blind_request", "estimate_write", "grader_request", "live_grader", "parse_blind", "parse_grader", "parse_writer", "plan_write",
           "run_write", "sources_block", "writer_request", "BLIND_MODEL", "GRADER_MODEL", "HARD_CAP_USD", "PRICES", "WRITER_MODEL", "Budget",
           "BudgetExceeded", "Caller", "LiveError", "Recording", "actual_usd", "estimate_usd", "make_client", "request_hash"]
