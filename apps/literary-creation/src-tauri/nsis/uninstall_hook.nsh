!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $DeleteAppDataCheckboxState = 1
    DetailPrint "清理 LiteraryCreation 运行期数据..."
    RMDir /r "$LOCALAPPDATA\LiteraryCreation\data"
    DetailPrint "LiteraryCreation 运行期数据已清理"
  ${EndIf}
!macroend
