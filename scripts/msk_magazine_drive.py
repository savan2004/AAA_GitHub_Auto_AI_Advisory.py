"""Optional private Drive backup of one mechanically valid UNAPPROVED magazine.

No posting, approval, public sharing, overwriting or deletion. Independent of
the bot's stock archive. Secrets are supplied only by the scheduled runner.
"""
import argparse
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

import requests
from scripts.msk_magazine_monthly import audit_pdf

FOLDER = "1nOiraLejVbO7kEyrAtAnKcMdIwucfOVL"
OWNER = "aiautofintech@gmail.com"
API = "https://www.googleapis.com/drive/v3"
MAX_BYTES = 15_000_000
TAG = "msk-magazine-draft-v1"
FIELDS = "id,parents,mimeType,trashed,size,md5Checksum,appProperties"


def verified_payload(issue_dir, issue, run_id):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", issue) or not run_id.isdecimal():
        raise ValueError("Invalid issue or run")
    path = issue_dir / f"MSK_{issue}_EDITORIAL_DRAFT.pdf"
    gate = json.loads((issue_dir / "release_gate.json").read_text())
    manifest = json.loads((issue_dir / "run_manifest.json").read_text())
    if (path.is_symlink() or not path.is_file()
            or not 25_000 <= path.stat().st_size <= MAX_BYTES):
        raise ValueError("Magazine file missing or oversize")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if (gate.get("issue") != issue or manifest.get("issue") != issue
            or str(manifest.get("run_id")) != run_id
            or gate.get("pdf_sha256") != digest or manifest.get("pdf_sha256") != digest
            or gate.get("editorial_approved") is not False
            or manifest.get("editorial_approved") is not False
            or gate.get("publication_allowed") is not False
            or manifest.get("publication_allowed") is not False
            or gate.get("page_count") != 16
            or not gate.get("mechanical_pdf_checks", {}).get("passed")
            or not audit_pdf(path, issue=issue)["passed"]):
        raise ValueError("Private draft gate failed")
    return raw, {"archive": TAG, "issue": issue, "runId": run_id,
                 "sha256": digest, "status": "UNAPPROVED_DRAFT"}


def archive(issue_dir, issue, run_id):
    if (os.getenv("MSK_MAGAZINE_DRIVE_ENABLED") != "1"
            or os.getenv("MSK_MAGAZINE_DRIVE_FOLDER_ID") != FOLDER):
        return False
    raw, props = verified_payload(issue_dir, issue, run_id)
    credentials = {k: os.environ["MSK_DRIVE_" + k.upper()]
                   for k in ("client_id", "client_secret", "refresh_token")}
    token = requests.post("https://oauth2.googleapis.com/token",
                          data={**credentials, "grant_type": "refresh_token"},
                          timeout=(4, 10))
    token.raise_for_status()
    with requests.Session() as http:
        http.headers["Authorization"] = "Bearer " + token.json()["access_token"]

        def call(method, url, **kwargs):
            response = http.request(method, url, timeout=(4, 20), **kwargs)
            response.raise_for_status()
            return response.json()

        folder = call("GET", API + "/files/" + FOLDER,
                      params={"fields": "id,mimeType,trashed,shared,owners(emailAddress)"})
        if (folder.get("id") != FOLDER or folder.get("shared") is not False
                or folder.get("trashed") is not False
                or folder.get("mimeType") != "application/vnd.google-apps.folder"
                or [x.get("emailAddress") for x in folder.get("owners", [])] != [OWNER]):
            raise ValueError("Private magazine folder not verified")

        def check(item):
            if (item.get("parents") != [FOLDER] or item.get("trashed") is not False
                    or item.get("mimeType") != "application/pdf"
                    or item.get("appProperties") != props
                    or int(item["size"]) != len(raw)
                    or item.get("md5Checksum") != hashlib.md5(raw).hexdigest()):
                raise ValueError("Archive metadata mismatch")
            with http.get(API + "/files/" + item["id"], params={"alt":"media"},
                          timeout=(4,20), stream=True) as response:
                response.raise_for_status()
                buf = bytearray()
                for block in response.iter_content(65536):
                    buf.extend(block)
                    if len(buf) > MAX_BYTES:
                        raise ValueError("Archive exceeds byte limit")
                if bytes(buf) != raw:
                    raise ValueError("Archive readback mismatch")
            return True

        matches = call("GET", API + "/files", params={
            "q": f"'{FOLDER}' in parents and trashed = false and "
                 f"appProperties has {{ key='archive' and value='{TAG}' }} and "
                 f"appProperties has {{ key='runId' and value='{run_id}' }}",
            "fields": f"nextPageToken,incompleteSearch,files({FIELDS})",
            "pageSize": 100})
        if matches.get("incompleteSearch") or matches.get("nextPageToken"):
            raise ValueError("Incomplete archive lookup")
        rows = matches.get("files", [])
        if len(rows) == 1:
            return check(rows[0])
        if rows:
            raise ValueError("Duplicate magazine revision; no mutation")
        meta = {"name": f"MSK_{issue}_UNAPPROVED_{run_id}.pdf",
                "mimeType":"application/pdf", "parents":[FOLDER],
                "appProperties": props}
        boundary = uuid.uuid4().hex
        body = (("--" + boundary + "\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                 + json.dumps(meta) + "\r\n--" + boundary
                 + "\r\nContent-Type: application/pdf\r\n\r\n").encode()
                + raw + ("\r\n--" + boundary + "--\r\n").encode())
        uploaded = call("POST", "https://www.googleapis.com/upload/drive/v3/files",
                        params={"uploadType":"multipart","fields":FIELDS},
                        headers={"Content-Type":"multipart/related; boundary=" + boundary},
                        data=body)
        return check(call("GET", API + "/files/" + uploaded["id"],
                          params={"fields":FIELDS}))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--issue-dir", type=Path, required=True)
    p.add_argument("--issue", required=True)
    p.add_argument("--run-id", required=True)
    args = p.parse_args()
    try:
        result = archive(args.issue_dir, args.issue, args.run_id)
        print("MSK_MAGAZINE_DRIVE_PRIVATE_DRAFT_ARCHIVED" if result
              else "MSK_MAGAZINE_DRIVE_DISABLED")
    except Exception as exc:
        # Never echo provider responses, credentials or authorization URLs.
        print("MSK_MAGAZINE_DRIVE_FAILED type=" + type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
