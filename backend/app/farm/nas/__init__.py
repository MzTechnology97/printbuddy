"""NAS share configured from Settings, mirrored read-only into the data dir.

The container can't mount CIFS (unprivileged LXC), so an in-app SMB client copies
one subfolder of the share one way (size + mtime) into a local mirror; the
watched-folder logic of ``farm.library_sync`` then works on that mirror.
"""
