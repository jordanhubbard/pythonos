"""Video Toaster helpers — wipe / reveal using the display window."""


def wipe_step(view, t: float, duration: float) -> None:
    """Advance a vertical wipe by growing ``diw_stop``.

    t=0 shows almost nothing; t>=duration shows the full View height.
    """
    if duration <= 0:
        view.diw_stop = view.height - 1
        return
    frac = t / duration
    if frac < 0:
        frac = 0.0
    if frac > 1:
        frac = 1.0
    view.diw_start = 0
    view.diw_stop = int(frac * (view.height - 1))
