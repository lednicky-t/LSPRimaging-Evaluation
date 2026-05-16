# Data Layout

This project is designed to keep large image datasets outside the Git repository.

Recommended local layout:

- the code repo can live anywhere
- keep datasets and exports in any separate folder you prefer

The app looks for a dataset folder in this order:

1. `LSPR_DATA_DIR`
2. `LSPR_DEFAULT_DATASET_DIR`
3. a project-specific default dataset folder, such as `data/One_frame`
4. `data/datasets/One_frame`
5. `data/One_frame`
6. `One_frame`

If you want to use a different folder, set `LSPR_DATA_DIR` to that path before launching the app.

If `LSPR_DATA_DIR` is not set, the app also honors `LSPR_DEFAULT_DATASET_DIR`
as a secondary override.
