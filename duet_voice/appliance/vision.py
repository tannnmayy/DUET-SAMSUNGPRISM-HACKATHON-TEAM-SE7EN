"""Optional camera helper for reading an appliance error code.

Not registered as a Round-1 tool. The live agent does not depend on this
module. A later round can call `read_error_code` and pass the returned code
into `get_troubleshooting_steps` — never the other way around, and never by
guessing a code from a blurry frame.
"""

from __future__ import annotations

from typing import Optional


def read_error_code(image_bytes: bytes) -> Optional[str]:
    """Return a code only when a real vision backend is wired.

    The default is unavailable: callers must treat a missing code as missing,
    not as a diagnosis.
    """
    raise NotImplementedError(
        "Camera error-code reading is optional and is not enabled for Round 1."
    )
