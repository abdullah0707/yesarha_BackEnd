def L(ar: str, en: str) -> dict:
    """
    Shorthand to build a bilingual label object used in the manifest
    and field schemas, so the dashboard can render Arabic/English
    without any extra code on its side.

    Example: L("اسم الباقة", "Plan Name")
    -> {"ar": "اسم الباقة", "en": "Plan Name"}
    """
    return {"ar": ar, "en": en}
