#!/usr/bin/env python3
"""Provision private Supabase demo accounts and import the supplied study archive."""

import argparse
import json
import os
import secrets
from pathlib import Path
from time import sleep

import requests

import config
from synthetic_archive import load_bundle


def credentials(path, patients):
    if path.exists():
        if path.stat().st_mode & 0o077:
            raise ValueError("Restrict the credential file to mode 600 before using it.")
        return json.loads(path.read_text())
    logins = {
        patient: {"email": f"{patient.lower()}@neurosync.example.com",
                  "password": secrets.token_urlsafe(24)}
        for patient in patients
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(logins, file, indent=2)
        file.write("\n")
    return logins


def import_patients(bundle, logins, admin_key):
    headers = {"apikey": admin_key, "Accept": "application/json"}
    if not admin_key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {admin_key}"

    with requests.Session() as client:
        client.headers.update(headers)

        def request(method, path, **params):
            for attempt in range(4):
                try:
                    response = client.request(method, config.SUPABASE_URL + path, timeout=(5, 35), **params)
                except (requests.ConnectionError, requests.Timeout):
                    if attempt == 3:
                        raise RuntimeError(f"{method} {path} failed to reach Supabase.") from None
                else:
                    if response.status_code < 400:
                        return response
                    if response.status_code not in (429, 500, 502, 503, 504) or attempt == 3:
                        raise RuntimeError(f"{method} {path} failed with HTTP {response.status_code}.")
                sleep(2 ** attempt)

        def find_accounts():
            by_email = {}
            page = 1
            while True:
                result = request("GET", "/auth/v1/admin/users",
                                 params={"page": page, "per_page": 100}).json()["users"]
                by_email.update((user.get("email"), user) for user in result)
                if len(result) < 100:
                    return by_email
                page += 1

        known = find_accounts()
        for patient, dataset in bundle.items():
            login = logins[patient]
            user = known.get(login["email"])
            if user is None:
                user = request("POST", "/auth/v1/admin/users", json={
                    **login, "email_confirm": True,
                    "user_metadata": {"username": "Patient " + patient},
                    "app_metadata": {"neurosync_demo_patient": patient},
                }).json()
            elif user.get("app_metadata", {}).get("neurosync_demo_patient") != patient:
                raise ValueError(f"Refusing to repurpose the existing account for {patient}.")

            profile = {key: value for key, value in dataset.items() if key != "samples"}
            request("POST", "/rest/v1/neurosync_demo_patients",
                    params={"on_conflict": "patient_id"}, json={**profile, "owner_id": user["id"]},
                    headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
            samples = dataset["samples"]
            for start in range(0, len(samples), 500):
                request("POST", "/rest/v1/neurosync_demo_samples", params={"on_conflict": "patient_id,recorded_at"},
                        headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                        json=samples[start:start + 500])
            result = request("GET", "/rest/v1/neurosync_demo_samples", headers={"Prefer": "count=exact"},
                             params={"patient_id": f"eq.{patient}", "select": "recorded_at", "limit": 1})
            count = int(result.headers["Content-Range"].rsplit("/", 1)[-1])
            if count != len(samples):
                raise RuntimeError(f"{patient} has {count} rows; expected {len(samples)}.")
            request("PATCH", "/rest/v1/neurosync_demo_patients", params={"patient_id": f"eq.{patient}"},
                    json={"ready": True}, headers={"Prefer": "return=minimal"})
            print(f"{patient}: {count:,} recordings verified", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--credentials", type=Path, default=Path(config.BASE_DIR) / ".demo-patient-logins.json")
    parser.add_argument("--check", action="store_true", help="Parse and validate without writing to Supabase.")
    args = parser.parse_args()
    bundle = load_bundle(args.folder)
    if args.check:
        for patient, dataset in bundle.items():
            print(patient, dataset["sample_count"], len(dataset["events"]), len(dataset["imu_windows"]))
        return
    key = os.environ.get("SUPABASE_ADMIN_KEY")
    if not key:
        parser.error("Set SUPABASE_ADMIN_KEY in the importer environment.")
    import_patients(bundle, credentials(args.credentials, bundle), key)


if __name__ == "__main__":
    main()
