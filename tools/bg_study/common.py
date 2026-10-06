import os

import numpy as np
import tifffile

D = r"C:\Users\Admin\Desktop\Data_PyTest\04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2\images"
X0, Y0, W, H = 16, 18, 1269, 742


def load(wl, cube):
    a = tifffile.imread(os.path.join(D, f"imLCTFatWL{wl}Frame{cube}.tiff"))
    return a[Y0 : Y0 + H, X0 : X0 + W].astype(np.float32)
