"""Offline, fail-closed validation of dated public-source market observations.

A validated snapshot is not an AiAutoUpdate handoff, stock-universe coverage
certificate, or authorization to publish an MSK magazine.
"""
from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
import json

class EvidenceError(ValueError):
    pass

def validate_snapshot(snapshot, *, today=None):
    # Market-day cutoffs use IST, not the host/container timezone.
    ist = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(ist).date() if today is None else today
    if not isinstance(snapshot, dict) or snapshot.get("status") != "PARTIAL_PUBLIC_SNAPSHOT":
        raise EvidenceError("Only explicitly partial source snapshots are accepted")
    if snapshot.get("release_approved") is not False:
        raise EvidenceError("Snapshot must not claim release approval")
    observations = snapshot.get("observations")
    if not isinstance(observations, list) or not observations:
        raise EvidenceError("Missing source observations")
    seen = set()
    for record in observations:
        if not isinstance(record, dict):
            raise EvidenceError("Invalid observation")
        if record.get("source_verified") is not False:
            raise EvidenceError("Partial snapshot cannot assert independently verified source")
        key = record.get("indicator")
        if not isinstance(key, str) or not key.strip() or key in seen:
            raise EvidenceError("Missing or duplicate indicator")
        seen.add(key)
        observed = record.get("observed_on")
        try:
            if not isinstance(observed, str) or date.fromisoformat(observed).isoformat() != observed:
                raise ValueError()
            if date.fromisoformat(observed) > today:
                raise ValueError()
        except ValueError as exc:
            raise EvidenceError("Invalid or future observation date") from exc
        checked = record.get("checked_at")
        try:
            timestamp = datetime.fromisoformat(checked)
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError()
            if timestamp.astimezone(timezone.utc) > datetime.now(timezone.utc):
                raise ValueError()
            # Compare source observation and retrieval in the market timezone.
            # A UTC retrieval just after midnight can still be the prior market day
            # in another offset; comparing the raw sender civil date is unsafe.
            ist = timezone(timedelta(hours=5, minutes=30))
            if date.fromisoformat(observed) > timestamp.astimezone(ist).date():
                raise ValueError()
        except (TypeError, ValueError) as exc:
            raise EvidenceError("Missing timezone-aware retrieval timestamp") from exc
        url = record.get("source_url")
        parsed = urlsplit(url) if isinstance(url, str) else None
        try:
            port = parsed.port if parsed else None
        except ValueError as exc:
            raise EvidenceError("Invalid source URL port") from exc
        if (not parsed or parsed.scheme != "https" or
                parsed.hostname not in {"www.nseindia.com", "rbi.org.in", "www.rbi.org.in"} or
                parsed.username is not None or parsed.password is not None or
                port not in (None, 443) or parsed.fragment):
            raise EvidenceError("Source must be a clean official publisher URL")
        if any(marker in unquote(url).lower() for marker in ("{", "}", "<", ">")):
            raise EvidenceError("Unresolved source URL template is not evidence")
        if not isinstance(record.get("source_note"), str) or len(record["source_note"].strip()) < 15:
            raise EvidenceError("Source observation requires provenance note")
        value = record.get("value")
        try:
            if type(value) not in (str, int, float) or not Decimal(str(value)).is_finite():
                raise InvalidOperation()
        except (InvalidOperation, ValueError) as exc:
            raise EvidenceError("Observation must have finite numeric value") from exc
        if not isinstance(record.get("unit"), str) or not record["unit"].strip():
            raise EvidenceError("Observation unit missing")
    coverage = snapshot.get("coverage")
    if coverage != {"universe_verified": False, "constituents_checked": 0}:
        raise EvidenceError("Partial public snapshot cannot assert index-wide coverage")
    return True

def publication_observations(snapshot):
    """Return only independently evidenced observations; fail closed otherwise.

    Public snapshot values are leads for human research, never magazine facts.
    The current partial snapshot explicitly contains no verified observations.
    """
    validate_snapshot(snapshot)
    raise EvidenceError(
        "No independently verified financial observations: "
        "partial public snapshot cannot populate magazine research"
    )

def load_snapshot(path):
    with open(path, encoding="utf-8") as stream:
        return validate_snapshot(json.load(stream))
