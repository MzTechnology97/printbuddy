"""Fork: the "queue job started" notification after a Klipper start.

A plain G-code (what Klipper printers get) has no print time in its archive, so the time came
from ``LibraryFile.print_time_seconds``, which doesn't exist: the AttributeError aborted the
queue check right after the print started, so the notification and the MQTT relay were skipped.
"""

from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.services.print_scheduler import PrintScheduler


def test_klipper_gcode_takes_the_time_from_its_metadata():
    archive = PrintArchive(filename="K1-part-plate-1.gcode", print_time_seconds=None)
    gcode = LibraryFile(filename="K1-part-plate-1.gcode", file_metadata={"print_time_seconds": 5040})
    assert not hasattr(gcode, "print_time_seconds")  # what the old code read: AttributeError
    assert PrintScheduler._estimated_time(archive, gcode) == 5040


def test_no_time_anywhere_is_none_not_an_error():
    archive = PrintArchive(filename="part.gcode", print_time_seconds=None)
    assert PrintScheduler._estimated_time(archive, LibraryFile(filename="part.gcode")) is None
    assert PrintScheduler._estimated_time(None, None) is None


def test_the_archive_time_comes_first():
    archive = PrintArchive(filename="part.gcode.3mf", print_time_seconds=3600)
    lib_file = LibraryFile(filename="part.gcode.3mf", file_metadata={"print_time_seconds": 10})
    assert PrintScheduler._estimated_time(archive, lib_file) == 3600
