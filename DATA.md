# Data Layout

This project is designed to keep large image datasets outside the Git repository.

Recommended local layout:

- `C:\Users\lednicky\Desktop\python\LSPRimaging` for the code repo
- `C:\Users\lednicky\Desktop\python\LSPRimaging_data` for datasets and exports

The app looks for a dataset folder in this order:

1. `LSPR_DATA_DIR`
2. `LSPR_DEFAULT_DATASET_DIR`
3. `C:\Users\lednicky\Desktop\python\LSPRimaging_data\One_frame`
4. `data/datasets/One_frame`
5. `data/One_frame`
6. `One_frame`

If you want to use a different folder, set `LSPR_DATA_DIR` to that path before launching the app.
