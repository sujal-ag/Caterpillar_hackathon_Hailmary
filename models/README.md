# models/

Trained model artifacts, committed directly (hackathon simplification — a real
production system would not commit binary model weights to git, but for this
demo, "cloud training happens nightly, edge box gets the file via git pull" is
a fine simplification to narrate).

- eta_v1.joblib
- anomaly_excavator_v1.joblib
- anomaly_wheel_loader_v1.joblib
- manifest.json — versions + sha256 for reference only (the real source of
  truth once registered is the `model_registry` SQL table, not this file)

These are NOT auto-loaded by the backend just by sitting here. After pulling,
run `backend/tools/register_model.py` once per file (see main README) to copy
each into MODELS_DIR/{name}/{version} and register it in model_registry, then
restart edge-api.
