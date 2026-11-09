"""Fixed mobile emulation from the installed Playwright device registry."""
from copy import deepcopy


BM_MOBILE_DEVICE='Pixel 7'


def bm_mobile_options(playwright):
    # Device descriptors also contain default_browser_type, which is not a
    # launch_persistent_context argument. Only pass documented emulation fields.
    descriptor=playwright.devices[BM_MOBILE_DEVICE]
    return {key:deepcopy(descriptor[key]) for key in (
        'viewport','user_agent','device_scale_factor','is_mobile','has_touch')}
