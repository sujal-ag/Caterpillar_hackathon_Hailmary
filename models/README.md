# models/

Trained model artifacts land here before a demo/integration run, copied from
the separate ML training repo. Expected filenames (P1 convention, not yet
referenced by backend code since backend/edge/ml/adapter.py doesn't exist yet):

- eta_v1.joblib
- anomaly_excavator_v1.joblib
- anomaly_wheel_loader_v1.joblib

See ml_runtime/README.md for the internal bundle format each file must contain.
Do not commit real trained weights to this scaffold commit — this is a
placeholder until the training pipeline produces real files.
