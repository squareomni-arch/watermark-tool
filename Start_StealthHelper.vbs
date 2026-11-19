Option Explicit

' Nhap vao de chay ngam Stealth Helper:
'   - Khong mo cua so terminal
'   - run.bat se tu tim/kich hoat Python roi phong main.py bang pythonw.exe

Dim fso, shell, appDir, batPath

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
batPath = fso.BuildPath(appDir, "run.bat")

shell.Environment("Process")("STEALTH_SILENT") = "1"
shell.Run """" & batPath & """", 0, False
