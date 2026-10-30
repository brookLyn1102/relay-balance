' 启动「中转管家」面板（不弹黑框）
' 双击本文件即可；已在运行时不会重复开第二个。

Dim sh, fso, here, pyw, script
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)
script = fso.BuildPath(here, "balance_widget.py")

' 优先用系统 PATH 里的 pythonw，找不到再试常见的按用户安装位置
pyw = "pythonw.exe"
If Not fso.FileExists(shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe")) Then
    pyw = "pythonw.exe"
End If

sh.CurrentDirectory = here
sh.Run """" & pyw & """ """ & script & """", 0, False
