# Neurosync AI

Run the Flask app with Python 3.11 or later.

```sh
python -m pip install -r requirements.txt
python app.py
```

Open [Neurosync AI](http://127.0.0.1:5000) and sign in with a Supabase Auth account. The repository workspace uses a local `.env`. On another machine, set these values in the environment or in a local `.env`.

| Variable | Purpose |
| --- | --- |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_PUBLIC_KEY` | Supabase publishable key or legacy anon key |
| `SECRET_KEY` | Stable random Flask signing key |
| `PHANTOMLIMB_ORIGIN` | App origin used in confirmation and reset links. Required when serving outside localhost. |
| `PHANTOMLIMB_DEFAULT_DEVICE` | Initial device selection for new profiles. Defaults to `C6525`. |

In Supabase **Authentication → URL Configuration**, allow both callbacks for your app origin. For the development server they are:

- `http://127.0.0.1:5000/auth/callback/signup`
- `http://127.0.0.1:5000/auth/callback/recover`

Use the same origin in the browser and in the allowlist. If you open the app through `localhost`, allow the two callbacks for that host as well. Keep `{{ .ConfirmationURL }}` in the Supabase confirmation and recovery email templates. Open links in the browser that started the request. Supabase sends the messages using the project's configured mail provider. [Supabase PKCE flow](https://supabase.com/docs/guides/auth/sessions/pkce-flow)

The dashboard, signal history, and sensor summaries fetch readings from `public.phantom_limb_telemetry`. The patient diary and archived web button events use local SQLite. A confirmed Supabase account with the same email links to the corresponding existing local diary. The local database is migrated at startup.

## Restricting telemetry reads

The existing project was reachable with the shared anonymous key when checked. For private wearable data, run [supabase_schema.sql](supabase_schema.sql) in the Supabase SQL Editor, then assign each authorized user's Auth UUID to their device. These steps require project administrator access and will replace the former public read policy. Keep the firmware's existing INSERT-only integration compatible with `Prefer: return=minimal`.

```sql
INSERT INTO public.phantom_limb_device_access (user_id, device_id)
VALUES ('REPLACE-WITH-AUTH-USER-UUID', 'C6525')
ON CONFLICT DO NOTHING;
```

Choosing a device ID in Settings filters the dashboard. The Supabase policy determines whether that account can read the device. The compatibility ingestion policy still accepts anonymous inserts. Use device-specific ingestion credentials before relying on readings from a shared project.

## Physical button events

The ESP32 uploads the physical SOS button state in `system.battery_voltage`, mirrored into the table’s `battery_voltage` column. A value of `0` is released and `1` is pressed. This slot carries a button flag for these packets.

The first uploaded `1` of a press creates a pain stamp using the Supabase receipt timestamp and the measurements in that packet. Further `1` packets stay part of the same press until a `0` is received. History and CSV show the state for each packet and mark the first packet of each press. The pain diary and clinician workspace display the same stamps and sensor snapshots.

## Expert Dashboard

A Supabase administrator sets expert roles and patient assignments in the account’s Auth **app metadata**. Use the patient’s Supabase Auth UUID and the device identifier for each assignment. Experts sign in at `/expert/login`.

```json
{
  "neurosync_doctor": true,
  "neurosync_patients": [
    { "user_id": "REPLACE-WITH-PATIENT-AUTH-UUID", "device_id": "C6525" }
  ]
}
```

For live devices, the patient must have signed in once to link their local diary account. Grant the expert’s Auth UUID access through the Supabase device policy above. For archived patient IDs P001–P003, the Flask server checks the assignment against the archive owner before reading from the two archive tables. Set `SUPABASE_ADMIN_KEY` in the server environment or private local `.env` to enable those expert archive reads. Keep this key on the server and out of distributed desktop builds. Have experts sign back in after editing a role or assignment. [Supabase guidance on trusted app metadata](https://supabase.com/docs/guides/database/postgres/row-level-security#authjwt)

## Synthetic patient archive

Three dedicated Supabase logins are assigned to patient P001, P002, or P003. Their recordings are shown in Signal History. Each archive is bound to its Auth user through row-level security, and the app takes the patient ID from administrator-managed app metadata. Signal History uses the archived recording dates in IST. The import keeps the original minute records, patient profiles, event logs, daily summaries, and 50 Hz IMU windows. Synthetic SOS flags come from the CSV's `sos_button` field. Demo Signal History and CSV exports use the live telemetry columns, with reproducible synthetic sensor traces and firmware-scored quiet and elevated periods. The original CSV values remain in the archive.

The [database migration](synthetic_patients.sql) provides the private archive tables. Run `python import_synthetic_patients.py --check PATH_TO_CSV_FOLDER` to validate incoming files. The importer uses a server-side `SUPABASE_ADMIN_KEY` from its environment and creates a private `.demo-patient-logins.json` file. A repeat import uses the same patient email and timestamp keys. The website runs with its public Supabase key and each signed-in user's access token.

[Firmware scoring and model-training notes](docs/firmware-scoring.md)
