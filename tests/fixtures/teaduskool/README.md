# Teaduskool calendar CSV — REAL, trimmed

`kalender.csv` is the public CSV behind <https://kalender.teaduskool.ut.ee/>
(`https://tk-kalender-files.s3.eu-central-1.amazonaws.com/kalender.csv`), downloaded on
2026-09-27 (its `kalender_last_updated.txt` said 25.09.2026 15:03). It was cut from 1,357 to 312
lines by keeping every group and column line, every row from 2025-09 on, every quoted row and a
few older ones. Line endings (CRLF) and order are as downloaded.

The real file has only ISO dates today; the `dd.mm.yyyy` and `dd/mm/yyyy` forms the calendar
page also reads are tested with small hand-written CSVs.
