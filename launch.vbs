Option Explicit
Dim files, shell, root, python, candidate
Set files = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = files.GetParentFolderName(WScript.ScriptFullName)
python = ""
For Each candidate In Array(".venv-rocm", ".venv")
    If files.FileExists(files.BuildPath(root, candidate & "\Scripts\pythonw.exe")) Then
        python = files.BuildPath(root, candidate & "\Scripts\pythonw.exe")
        Exit For
    End If
Next
If python = "" Then
    WScript.Echo "No Python environment found. Run scripts\setup-venv.ps1 first."
    WScript.Quit 1
End If
If WScript.Arguments.Count > 0 Then
    If WScript.Arguments(0) = "--check" Then
        WScript.Echo "ready"
        WScript.Quit 0
    End If
    WScript.Quit 2
End If
shell.CurrentDirectory = root
shell.Run Chr(34) & python & Chr(34) & " " & Chr(34) & files.BuildPath(root, "launcher.pyw") & Chr(34), 0, False
