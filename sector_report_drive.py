"""Separate sector archive. No stock FIFO and no sector deletions."""
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from research_pdf_drive import Drive, SECTOR_FOLDER, SECTOR_TAG, _managed, _now, _LOCK

LOG = logging.getLogger(__name__)
MAX_REPORTS = 500  # safety stop, not a deletion policy


def enabled():
    return (os.getenv("MSK_SECTOR_DRIVE_ENABLED") == "1"
            and os.getenv("MSK_SECTOR_DRIVE_FOLDER_ID") == SECTOR_FOLDER)


def _summary(value):
    if (not isinstance(value, list) or not value or
            not all(isinstance(x, str) and x.strip() for x in value)):
        raise ValueError("Missing sector summary")
    return value


def _metadata(item, session):
    if not _managed(item, SECTOR_FOLDER, SECTOR_TAG):
        raise ValueError("Unsafe sector file")
    props = item["appProperties"]
    if props.get("session") != str(session):
        raise ValueError("Wrong sector session")
    description = item["description"]
    if hashlib.sha256(description.encode()).hexdigest() != props.get("metadataSha"):
        raise ValueError("Sector metadata integrity mismatch")
    value = json.loads(description)
    from sector_report_snapshot import _check_metadata
    _check_metadata(value["coverage"], expected=session)
    return _summary(value["summary"])


def load_sector_cloud(*, output_dir):
    if not enabled():
        return None
    client = None
    path = None
    try:
        from sector_report_snapshot import _expected_session, _check_file
        session = _expected_session()
        with _LOCK:
            client = Drive(SECTOR_FOLDER, SECTOR_TAG)
            matches = [x for x in client.list()
                       if x["appProperties"].get("session") == str(session)]
            if len(matches) != 1:
                return None
            item = matches[0]
            summary = _metadata(item, session)
            raw = client.download(item)
            if session != _expected_session():
                return None
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix="Index_Sectoral_Update_cloud_",
                                         suffix=".pdf", dir=output_dir)
            path = Path(name)
            with os.fdopen(fd, "wb") as out:
                out.write(raw)
            _check_file(path, session=session)
            result = (str(path), summary)
            path = None
            return result
    except Exception as exc:
        LOG.warning("MSK_SECTOR_DRIVE_READ_FAILED type=%s", type(exc).__name__)
        return None
    finally:
        if path is not None:
            path.unlink(missing_ok=True)
        if client:
            client.close()


def store_sector_cloud(report, path, summary):
    if not enabled():
        return False
    client = None
    try:
        from sector_report_snapshot import _check_metadata, _check_file, _expected_session
        session = _check_metadata(report)
        _, size = _check_file(path, session=session)
        raw = Path(path).read_bytes()
        if len(raw) != size:
            return False
        coverage = {k: report[k] for k in ("asof", "index_coverage", "n_unique",
                                            "n_stocks", "n_configured") if k in report}
        description = json.dumps({"coverage": coverage, "summary": _summary(summary)},
                                 ensure_ascii=True, separators=(",", ":"))
        if len(description.encode()) > 25_000:
            return False
        props = {"archive": SECTOR_TAG, "session": str(session),
                 "generatedAt": _now().isoformat(),
                 "sha256": hashlib.sha256(raw).hexdigest(),
                 "metadataSha": hashlib.sha256(description.encode()).hexdigest()}
        with _LOCK:
            client = Drive(SECTOR_FOLDER, SECTOR_TAG)
            before = client.list()
            matches = [x for x in before if x["appProperties"].get("session") == str(session)]
            if len(matches) == 1:
                _metadata(matches[0], session)
                client.download(matches[0])
                return True
            if matches or len(before) >= MAX_REPORTS or session != _expected_session():
                return False
            new = client.upload({"name": f"MSK_Sector_{session}.pdf",
                "mimeType": "application/pdf", "parents": [SECTOR_FOLDER],
                "description": description, "appProperties": props}, raw)
            check = client.get(new["id"])
            if check.get("appProperties") != props or _metadata(check, session) != summary:
                raise ValueError("Sector upload metadata mismatch")
            return client.download(check) == raw
    except Exception as exc:
        LOG.warning("MSK_SECTOR_DRIVE_WRITE_FAILED type=%s", type(exc).__name__)
        return False
    finally:
        if client:
            client.close()
