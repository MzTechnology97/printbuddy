"""NAS share configured from Settings, used straight over SMB (no mount, no copy).

The container can't mount CIFS (unprivileged LXC), so an in-app SMB client lists
one subfolder of the share and reads the few 3MF entries the library needs
(``library.py``). Files are downloaded only when used (slicing, printing,
download), into a local cache with an LRU size limit (``cache.py``).
"""
