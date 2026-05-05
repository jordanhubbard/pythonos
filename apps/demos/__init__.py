"""apps.demos — Built-in graphics + audio demos."""

# Import order matters: each module's top-level register() call needs to
# fire when ``apps.demos`` is imported.
from apps.demos import bouncing_ball  # noqa: F401
from apps.demos import audio_tone     # noqa: F401
from apps.demos import starfield      # noqa: F401
from apps.demos import rainfall       # noqa: F401
from apps.demos import plasma         # noqa: F401
from apps.demos import paint          # noqa: F401
from apps.demos import life           # noqa: F401
