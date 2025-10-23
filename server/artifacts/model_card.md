
# Delay Prediction Model — Model Card

- Trained: 2025-10-23T05:29:52.660803Z
- Algorithm: sklearn HistGradientBoostingRegressor
- Features: ['hour', 'weekday', 'prev_delay_minutes', 'lon', 'lat', 'is_canceled', 'station_signature']
- Target: delay_minutes (clipped to [-20, 300])
- Split: 80/20 train/test

## Metrics (test)
- MAE (minutes): 1.02
- R^2: 0.793

## Notes
- `prev_delay_minutes` approximates delay propagation.
- `station_signature` is one-hot encoded; lon/lat included for spatial signal.
- Consider adding weather, line features, operator, and distance-to-next-stop.
    