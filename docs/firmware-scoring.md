# Neurosync AI firmware scoring

The supplied `CODE.ino` implements a weighted heuristic in `evaluatePainModel()`. Its 0–100 output is a device index. The displayed 0–10 device VAS is the same number rescaled. Neither measurement establishes a probability of pain or a patient's self-rated VAS.

Each valid input contributes a clamped value from 0 to 1, except for the two-part motion contribution. An absent or invalid reading contributes zero in the firmware. Temperature and humidity also require a valid DHT reading.

| Signal | Weight | Firmware contribution before weighting |
| --- | ---: | --- |
| Heart rate | 18% | Clamp `(bpm − 78) / 32` to 0–1 |
| HRV RMSSD | 15% | Clamp `(28 − RMSSD ms) / 20` to 0–1 |
| Perfusion index | 15% | Clamp `(1.10 − PI %) / 0.70` to 0–1 |
| Respiration rate | 12% | Clamp `(breaths/min − 18) / 10` to 0–1 |
| Humidity | 13% | Clamp `(humidity % − 55) / 25` to 0–1 |
| Temperature | 12% | Clamp `(temperature °C − 35.5) / 2.5` to 0–1 |
| Motion | 10% | Clamp `(posture score − 20) / 45` to 0–0.6, then add 0.4 if the jerk detector fires |
| Oxygen saturation | 5% | Clamp `(96 − SpO₂ %) / 6` to 0–1 |

With finger contact, the firmware sums the weighted contributions. It adds 0.15 if the heart-rate, temperature, and humidity contributions each exceed 0.50. This corresponds to readings above 94 bpm, 36.75 °C, and 67.5% humidity. The final score is clamped to 0–1, multiplied by 100 for the index and by 10 for the device VAS.

Without finger contact, the firmware instead uses `0.35 × motion + 0.30 × temperature + 0.35 × humidity` and uploads the classification `STANDBY`. With contact, the labels are `NORMAL` below 25, `MILD` from 25 to below 45, `MODERATE` from 45 to below 70, and `SEVERE` from 70. The default device alert uses a strict comparison against 45. The motor can also activate from other firmware triggers, including the physical BOOT override. The firmware uploads its state and classifications to Supabase. The supplied code does not dispatch a separate clinician notification at 70.

The physical BOOT button toggles a latched override. Turning it on saves current nonzero readings to the firmware's configured limits and activates vibration. Supabase receives the override flag in `battery_voltage`. The web app counts one pain stamp when it observes the transition to 1 and waits for 0 before counting another.

## Synthetic dataset and training

The demo files supply one heart rate and one interbeat interval per recorded minute, socket temperature and humidity, motion summaries, steps, SOS flags, and scenario labels. The separate 50 Hz IMU windows describe motion activities. There is no patient-rated pain score or matched beat-to-beat RMSSD, perfusion, SpO₂, or respiration data. A synthetic SOS press may represent an accidental press, dizziness, or a fall.

The demo dashboards use the live sensor fields and the same rule-based score. The importer generates reproducible quiet and elevated segments for each patient, preserving the original CSV columns separately from the generated telemetry.

From the webapp directory, train two Random Forest models using the same telemetry transformations as the Supabase archive importer.

```sh
python -m ml.train_patient_forest "/path/to/patient files"
```

The classifier predicts `pain_status` across the four device bands. The evaluation also reports F1 for detecting any band above `NORMAL`. The regressor predicts `pain_index` on its 0–100 scale. Both models take nine sensor readings as inputs. Patient IDs are used for grouping, and score-derived fields such as `vas_score` stay out of the inputs.

Each evaluation fold holds out one entire patient. The trainer then fits the saved models on all three patients and writes them with the feature names and fold metrics to `ml/models/patient_pain_forest.joblib`. The metrics measure agreement with the firmware scoring rule on generated recordings. Evaluating prediction of actual pain requires independent patient-reported labels.

The dashboard scores incoming and archived sensor readings with the saved Random Forest regressor. The gauge, history, and hardware-event snapshots display this experimental score on the 0–100 scale. A score requires finger contact and all nine inputs. Patient-entered diary ratings are recorded on their own 0–10 scale.

[Grouped cross-validation guidance from scikit-learn](https://scikit-learn.org/stable/modules/cross_validation.html#cross-validation-iterators-for-grouped-data)
