"""Files application: a thin host for the shared graphical file chooser.

Navigation and transfer:
    Up / Down       — move selection
    Enter           — descend into a directory (or display file size for files)
    Backspace       — go up one directory
    PgUp / PgDn     — page selection
    Host file drop  — import into the displayed/target directory
    Drag to Export  — copy the selected VFS file to the desktop host
    ESC             — close
"""

from kernel.gui.compositor import compositor
from kernel.gui.filechooser import choose_file
from apps import registry


async def main(argv=None, *args, **kwargs) -> None:
    argv = list(argv) if argv else []
    start = argv[0] if argv else "/"
    # Files always treats its argument as a directory. The generic chooser
    # also accepts file paths, where a missing trailing slash has different
    # meaning, so make the directory intent explicit here.
    if not start.endswith("/"):
        start += "/"
    path = await choose_file(title="Files", mode="open", path=start,
                             allow_transfer=True)
    if path:
        compositor.launch_app("editor", [path])


from apps._icons import files_icon

registry.register(
    name="files",
    description="Browse, edit, import, and export files",
    entry=main,
    icon_factory=files_icon,
)
