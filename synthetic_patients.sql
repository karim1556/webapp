BEGIN;

CREATE TABLE IF NOT EXISTS public.neurosync_demo_patients (
    patient_id text PRIMARY KEY CHECK (patient_id IN ('P001', 'P002', 'P003')),
    owner_id uuid NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    profile jsonb NOT NULL,
    daily_summary jsonb NOT NULL,
    events jsonb NOT NULL,
    imu_windows jsonb NOT NULL,
    first_recorded_at timestamptz NOT NULL,
    last_recorded_at timestamptz NOT NULL,
    sample_count integer NOT NULL CHECK (sample_count > 0),
    ready boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS public.neurosync_demo_samples (
    patient_id text NOT NULL REFERENCES public.neurosync_demo_patients(patient_id) ON DELETE CASCADE,
    recorded_at timestamptz NOT NULL,
    readings jsonb NOT NULL,
    PRIMARY KEY (patient_id, recorded_at)
);

ALTER TABLE public.neurosync_demo_patients ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.neurosync_demo_samples ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.neurosync_demo_patients, public.neurosync_demo_samples FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.neurosync_demo_patients, public.neurosync_demo_samples TO authenticated;
GRANT ALL ON public.neurosync_demo_patients, public.neurosync_demo_samples TO service_role;

DROP POLICY IF EXISTS demo_patient_owner ON public.neurosync_demo_patients;
CREATE POLICY demo_patient_owner ON public.neurosync_demo_patients
    FOR SELECT TO authenticated USING (ready AND owner_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS demo_samples_owner ON public.neurosync_demo_samples;
CREATE POLICY demo_samples_owner ON public.neurosync_demo_samples
    FOR SELECT TO authenticated USING (
        EXISTS (SELECT 1 FROM public.neurosync_demo_patients patient
                WHERE patient.patient_id = neurosync_demo_samples.patient_id
                  AND patient.ready AND patient.owner_id = (SELECT auth.uid()))
    );

COMMIT;
