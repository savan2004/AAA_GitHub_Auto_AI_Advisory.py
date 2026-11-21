"""Collect a bounded subset of secondary evidence for internal staging.

No customer profile, no report, no ranking, no persisted holdings. The result
cannot be promoted to a complete scan or to verified financial research.
"""
from dataclasses import dataclass
import time
from msk_ai_builder_primary import fetch_index, IndexMember
from msk_ai_builder_evidence import collect_secondary, EvidenceError, SecondarySnapshot

@dataclass(frozen=True)
class StageResult:
    universe_size: int
    requested: int
    attempted: int
    snapshots: tuple
    failures: tuple

    @property
    def coverage(self):
        return len(self.snapshots), self.universe_size

    @property
    def publication_status(self):
        return "WITHHELD"

def stage_secondary(*, index_provider=None, evidence_provider=None,
                    max_symbols=5, deadline_seconds=60, clock=time.monotonic):
    if type(max_symbols) is not int or not 1<=max_symbols<=20:
        raise ValueError("Batch size must be between 1 and 20")
    if type(deadline_seconds) not in (int,float) or not 1<=deadline_seconds<=120:
        raise ValueError("Invalid deadline")
    # Cooperative budget between requests; provider sockets may overrun.
    start=clock()
    members=tuple((index_provider or fetch_index)())
    if (len(members)!=500 or any(not isinstance(m,IndexMember) for m in members)
        or len({m.symbol for m in members})!=500 or len({m.isin for m in members})!=500):
        raise EvidenceError("INDEX_UNAVAILABLE")
    selected=sorted(members,key=lambda m:m.symbol)[:max_symbols]
    snapshots,failures=[],[]
    attempted=0
    for m in selected:
        remaining=deadline_seconds-(clock()-start)
        if remaining<1:
            break
        attempted+=1
        try:
            result=(evidence_provider or collect_secondary)(m.symbol,timeout=min(9,remaining/2))
            if not isinstance(result,SecondarySnapshot) or result.symbol!=m.symbol or result.publication_status!="SECONDARY_ONLY":
                raise EvidenceError("INVALID_EVIDENCE")
            if clock()-start>deadline_seconds:
                failures.append((m.symbol,"DEADLINE_EXCEEDED"))
                break
            snapshots.append(result)
        except EvidenceError as exc:
            failures.append((m.symbol,str(exc)))
        except Exception:
            failures.append((m.symbol,"PROVIDER_UNAVAILABLE"))
    return StageResult(500,len(selected),attempted,tuple(snapshots),tuple(failures))
