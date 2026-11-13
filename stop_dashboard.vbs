' Garmin Health Dashboard - stop the service started by start_dashboard.vbs.
Option Explicit
Dim shell, fso, root, pythonw, cmd
root = "C:\Users\cmcc\WorkBuddy\2026-11-12-09-38-14\garmin-health-dashboard"
pythonw = "C:\Users\cmcc\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe"

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = root

If Not fso.FileExists(pythonw) Then pythonw = "python.exe"

cmd = """" & pythonw & """ """ & root & "\launcher.py"" --stop"
shell.Run cmd, 0, False
