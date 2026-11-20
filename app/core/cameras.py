"""Camera full names by camera code (shared by all rovers).

Unknown codes fall back to the code itself (see ``camera_full_name``).
"""

from collections.abc import Iterable

CAMERA_NAMES: dict[str, str] = {
    # Legacy short codes (Curiosity, Opportunity, Spirit)
    "FHAZ": "Front Hazard Avoidance Camera",
    "RHAZ": "Rear Hazard Avoidance Camera",
    "MAST": "Mast Camera",
    "CHEMCAM": "Chemistry and Camera Complex",
    "MAHLI": "Mars Hand Lens Imager",
    "MARDI": "Mars Descent Imager",
    "NAVCAM": "Navigation Camera",
    "PANCAM": "Panoramic Camera",
    "MINITES": "Miniature Thermal Emission Spectrometer (Mini-TES)",
    "ENTRY": "Entry, Descent, and Landing Camera",
    # Spirit/Opportunity, not in the legacy API (PDS archive)
    "MI": "Microscopic Imager",
    # Curiosity instrument codes from the current feed
    "FHAZ_LEFT_A": "Front Hazard Avoidance Camera - Left A",
    "FHAZ_RIGHT_A": "Front Hazard Avoidance Camera - Right A",
    "FHAZ_LEFT_B": "Front Hazard Avoidance Camera - Left B",
    "FHAZ_RIGHT_B": "Front Hazard Avoidance Camera - Right B",
    "RHAZ_LEFT_A": "Rear Hazard Avoidance Camera - Left A",
    "RHAZ_RIGHT_A": "Rear Hazard Avoidance Camera - Right A",
    "RHAZ_LEFT_B": "Rear Hazard Avoidance Camera - Left B",
    "RHAZ_RIGHT_B": "Rear Hazard Avoidance Camera - Right B",
    "NAV_LEFT_A": "Navigation Camera - Left A",
    "NAV_RIGHT_A": "Navigation Camera - Right A",
    "NAV_LEFT_B": "Navigation Camera - Left B",
    "NAV_RIGHT_B": "Navigation Camera - Right B",
    "MAST_LEFT": "Mast Camera - Left",
    "MAST_RIGHT": "Mast Camera - Right",
    "CHEMCAM_RMI": "ChemCam Remote Micro Imager",
    # Perseverance
    "EDL_RUCAM": "Rover Up-Look Camera",
    "EDL_RDCAM": "Rover Down-Look Camera",
    "EDL_DDCAM": "Descent Stage Down-Look Camera",
    "EDL_PUCAM1": "Parachute Up-Look Camera A",
    "EDL_PUCAM2": "Parachute Up-Look Camera B",
    "NAVCAM_LEFT": "Navigation Camera - Left",
    "NAVCAM_RIGHT": "Navigation Camera - Right",
    "MCZ_RIGHT": "Mast Camera Zoom - Right",
    "MCZ_LEFT": "Mast Camera Zoom - Left",
    "FRONT_HAZCAM_LEFT_A": "Front Hazard Avoidance Camera - Left",
    "FRONT_HAZCAM_RIGHT_A": "Front Hazard Avoidance Camera - Right",
    "REAR_HAZCAM_LEFT": "Rear Hazard Avoidance Camera - Left",
    "REAR_HAZCAM_RIGHT": "Rear Hazard Avoidance Camera - Right",
    "SKYCAM": "MEDA Skycam",
    "SHERLOC_WATSON": "SHERLOC WATSON Camera",
    "SUPERCAM_RMI": "SuperCam Remote Micro Imager",
    "LCAM": "Lander Vision System Camera",
    # Ingenuity
    "HELI_NAV": "Navigation Camera",
    "HELI_RTE": "Color Camera",
}


def camera_full_name(code: str) -> str:
    """Return the full camera name, or the upper-case code when it is unknown."""
    normalized = code.upper()
    return CAMERA_NAMES.get(normalized, normalized)


# v1 only: a legacy short code of a Curiosity camera also selects the instrument codes of the
# current feed that start with the prefix (docs/LEGACY-API.md, "Камеры"). Other rovers and
# other codes match exactly.
LEGACY_CAMERA_GROUPS: dict[str, dict[str, str]] = {
    "curiosity": {
        "FHAZ": "FHAZ_",
        "RHAZ": "RHAZ_",
        "NAVCAM": "NAV_",
        "MAST": "MAST_",
        "CHEMCAM": "CHEMCAM_",
    },
}


def legacy_camera_codes(rover_id: str, camera: str, known_codes: Iterable[str]) -> list[str]:
    """Camera codes (upper case, sorted) out of ``known_codes`` that the v1 ``camera`` selects."""
    wanted = camera.upper()
    prefix = LEGACY_CAMERA_GROUPS.get(rover_id.lower(), {}).get(wanted)
    selected = {
        code
        for code in (known.upper() for known in known_codes)
        if code == wanted or (prefix is not None and code.startswith(prefix))
    }
    return sorted(selected)
