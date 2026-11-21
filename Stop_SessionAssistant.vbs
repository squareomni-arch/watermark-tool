Option Explicit

' Nhap vao de dung Session Assistant da chay ngam.

Dim fso, shell, appDir, pidFile, f, pid

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
pidFile = fso.BuildPath(appDir, "data\session_assistant.pid")

If fso.FileExists(pidFile) Then
    Set f = fso.OpenTextFile(pidFile, 1)
    pid = Trim(f.ReadLine)
    f.Close

    If IsNumeric(pid) Then
        shell.Run "taskkill /PID " & pid & " /F", 0, True
    End If

    fso.DeleteFile pidFile, True
End If
