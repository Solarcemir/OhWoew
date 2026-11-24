@echo off
rem Play heads-up against our bot (sparring\our_v17, the entry) in the browser at http://127.0.0.1:8800
rem Needs the kit's Python 3.10 environment in .venv (see README). Close this window to stop the table.
cd /d "%~dp0"
set BOT_ENV_ALLOW=SYSTEMROOT,WINDIR
".venv\Scripts\python.exe" tools\play_web.py --bot sparring\our_v17
pause
