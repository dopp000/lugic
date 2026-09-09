STATUS_COLORS = {
    "burn": 0xCF2F18,
    "bleed": 0xF65202,
    "tremor": 0xF99A00,
    "rupture": 0x82B61F,
    "sinking": 0x3195A5,
    "poise": 0x0769CE,
    "evasion": 0x99C24D,
    "counter": 0xC2412D,
    "charge": 0x8943A8,
}

DEFAULT_COLOR = 0x99AAB5


def get_status_color(status_name: str | None) -> int:
    """Returns the embed color for a status name, or the default gray if
    there's no active status or the name isn't recognized.
    """
    if status_name is None:
        return DEFAULT_COLOR
    return STATUS_COLORS.get(status_name.lower(), DEFAULT_COLOR)