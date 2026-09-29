; Скрипт создания установщика Inno Setup для DOCXдодыр v3.0
; Конфигурация: Windows x64 per-user (без прав администратора) с WebView2 детекцией

#define MyAppName "DOCXдодыр"
#ifndef MyAppVersion
#define MyAppVersion "3.0.1"
#endif
#define MyAppPublisher "DOCXdodyr Contributors"
#define MyAppURL "https://github.com/Kadmeia/DOCXdodyr"
#define MyAppSupportURL "https://t.me/pro_servitude"
#define MyAppExeName "DOCXdodyr.exe"
#define MyAppId "{{B42E784D-6780-4D56-A83A-2895DC569941}}"

[Setup]
; Уникальный стабильный идентификатор приложения для обновлений и удаления
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppSupportURL}
AppUpdatesURL={#MyAppURL}/releases

; Per-User установка: в локальный профиль текущего пользователя без прав администратора
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\DOCXdodyr
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; Выходной файл инсталлятора
OutputDir=..\dist
OutputBaseFilename=DOCXdodyr-Setup-{#MyAppVersion}-win64
SetupIconFile=..\assets\DOCXdodyr.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

; Современное оформление и максимальное ультра-сжатие
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes

; Поддерживаемая архитектура — строго 64-бит
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Метаданные и лицензия
LicenseFile=..\EULA.txt
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Инсталлятор DOCXдодыр для Windows x64
VersionInfoCopyright=© 2024–2026 DOCXdodyr. Все права защищены.

; Закрывать запущенное приложение перед обновлением
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "contextmenu_anon"; Description: "Добавить пункт «Обезличить в DOCXдодыр» в контекстное меню Проводника (для файлов и папок)"; GroupDescription: "Интеграция с Проводником Windows:"; Flags: checkedonce
Name: "contextmenu_restore"; Description: "Добавить пункт «Восстановить в DOCXдодыр» в контекстное меню Проводника (для файлов и папок)"; GroupDescription: "Интеграция с Проводником Windows:"; Flags: checkedonce
Name: "fileassoc_docx"; Description: "Ассоциировать с файлами Microsoft Word (*.docx)"; GroupDescription: "Ассоциации файлов:"
Name: "fileassoc_xlsx"; Description: "Ассоциировать с таблицами Excel (*.xlsx)"; GroupDescription: "Ассоциации файлов:"
Name: "fileassoc_pdf"; Description: "Ассоциировать с документами PDF (*.pdf)"; GroupDescription: "Ассоциации файлов:"

[Files]
; Исполняемые файлы и ресурсы onedir-сборки PyInstaller
Source: "..\dist\DOCXdodyr\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Встроенные языковые модели Tesseract OCR (rus + eng) для автономной работы без скачивания
Source: "..\resources\tessdata\*"; DestDir: "{app}\resources\tessdata"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Регистрация AppUserModelId для корректного отображения иконки и уведомлений в панели задач Windows 10/11
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\DOCXdodyr.Desktop.3.0"; ValueType: string; ValueName: "DisplayName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\DOCXdodyr.Desktop.3.0"; ValueType: string; ValueName: "IconUri"; ValueData: "{app}\{#MyAppExeName},0"; Flags: uninsdeletekey

; Ассоциация .docx
Root: HKCU; Subkey: "Software\Classes\.docx\OpenWithProgids"; ValueType: none; ValueName: "DOCXdodyr.Document.docx"; Tasks: fileassoc_docx; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\DOCXdodyr.Document.docx"; ValueType: string; ValueData: "Документ DOCX (DOCXдодыр)"; Tasks: fileassoc_docx; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\DOCXdodyr.Document.docx\DefaultIcon"; ValueType: string; ValueData: "{app}\{#MyAppExeName},0"; Tasks: fileassoc_docx; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\DOCXdodyr.Document.docx\shell\open\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc_docx; Flags: uninsdeletekey

; Ассоциация .xlsx
Root: HKCU; Subkey: "Software\Classes\.xlsx\OpenWithProgids"; ValueType: none; ValueName: "DOCXdodyr.Document.xlsx"; Tasks: fileassoc_xlsx; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\DOCXdodyr.Document.xlsx"; ValueType: string; ValueData: "Таблица XLSX (DOCXдодыр)"; Tasks: fileassoc_xlsx; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\DOCXdodyr.Document.xlsx\DefaultIcon"; ValueType: string; ValueData: "{app}\{#MyAppExeName},0"; Tasks: fileassoc_xlsx; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\DOCXdodyr.Document.xlsx\shell\open\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc_xlsx; Flags: uninsdeletekey

; Ассоциация .pdf
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: none; ValueName: "DOCXdodyr.Document.pdf"; Tasks: fileassoc_pdf; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\DOCXdodyr.Document.pdf"; ValueType: string; ValueData: "Документ PDF (DOCXдодыр)"; Tasks: fileassoc_pdf; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\DOCXdodyr.Document.pdf\DefaultIcon"; ValueType: string; ValueData: "{app}\{#MyAppExeName},0"; Tasks: fileassoc_pdf; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\DOCXdodyr.Document.pdf\shell\open\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc_pdf; Flags: uninsdeletekey

; Контекстное меню Проводника Windows (ПКМ по папке)
Root: HKCU; Subkey: "Software\Classes\Directory\shell\DOCXdodyr_anonymize"; ValueType: string; ValueData: "Обезличить DOCXdodyr"; Tasks: contextmenu_anon; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\shell\DOCXdodyr_anonymize"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName},0"; Tasks: contextmenu_anon; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\shell\DOCXdodyr_anonymize\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" --anonymize --headless --no-open-output ""%1"""; Tasks: contextmenu_anon; Flags: uninsdeletekey

Root: HKCU; Subkey: "Software\Classes\Directory\shell\DOCXdodyr_restore"; ValueType: string; ValueData: "Восстановить DOCXdodyr"; Tasks: contextmenu_restore; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\shell\DOCXdodyr_restore"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName},0"; Tasks: contextmenu_restore; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\shell\DOCXdodyr_restore\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" --restore --headless --no-open-output ""%1"""; Tasks: contextmenu_restore; Flags: uninsdeletekey

; Контекстное меню Проводника Windows (ПКМ внутри открытой папки)
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\DOCXdodyr_anonymize"; ValueType: string; ValueData: "Обезличить эту папку DOCXdodyr"; Tasks: contextmenu_anon; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\DOCXdodyr_anonymize"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName},0"; Tasks: contextmenu_anon; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\DOCXdodyr_anonymize\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" --anonymize --headless --no-open-output ""%V"""; Tasks: contextmenu_anon; Flags: uninsdeletekey

Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\DOCXdodyr_restore"; ValueType: string; ValueData: "Восстановить эту папку DOCXdodyr"; Tasks: contextmenu_restore; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\DOCXdodyr_restore"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName},0"; Tasks: contextmenu_restore; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\DOCXdodyr_restore\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" --restore --headless --no-open-output ""%V"""; Tasks: contextmenu_restore; Flags: uninsdeletekey

; Контекстное меню Проводника Windows (ПКМ по файлу)
Root: HKCU; Subkey: "Software\Classes\*\shell\DOCXdodyr_anonymize"; ValueType: string; ValueData: "Обезличить DOCXdodyr"; Tasks: contextmenu_anon; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\*\shell\DOCXdodyr_anonymize"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName},0"; Tasks: contextmenu_anon; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\*\shell\DOCXdodyr_anonymize\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" --anonymize --headless --no-open-output ""%1"""; Tasks: contextmenu_anon; Flags: uninsdeletekey

Root: HKCU; Subkey: "Software\Classes\*\shell\DOCXdodyr_restore"; ValueType: string; ValueData: "Восстановить DOCXdodyr"; Tasks: contextmenu_restore; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\*\shell\DOCXdodyr_restore"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName},0"; Tasks: contextmenu_restore; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\*\shell\DOCXdodyr_restore\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" --restore --headless --no-open-output ""%1"""; Tasks: contextmenu_restore; Flags: uninsdeletekey

; Удаление устаревших одиночных ключей
Root: HKCU; Subkey: "Software\Classes\Directory\shell\DOCXdodyr"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\DOCXdodyr"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\*\shell\DOCXdodyr"; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// Функция проверки наличия Microsoft Edge WebView2 Evergreen Runtime
function IsWebView2Installed(): Boolean;
var
  WebViewVersion: String;
begin
  Result := False;
  // Проверка в ветках реестра для текущего пользователя и машины
  if RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', WebViewVersion) then
  begin
    if (WebViewVersion <> '') and (WebViewVersion <> '0.0.0.0') then
      Result := True;
  end;
  if not Result and RegQueryStringValue(HKLM, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', WebViewVersion) then
  begin
    if (WebViewVersion <> '') and (WebViewVersion <> '0.0.0.0') then
      Result := True;
  end;
  if not Result and RegQueryStringValue(HKLM, 'Software\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', WebViewVersion) then
  begin
    if (WebViewVersion <> '') and (WebViewVersion <> '0.0.0.0') then
      Result := True;
  end;
end;

// Предупреждение при установке, если WebView2 не обнаружен
function InitializeSetup(): Boolean;
var
  ErrorCode: Integer;
begin
  Result := True;
  if not IsWebView2Installed() then
  begin
    if MsgBox('Для работы графического интерфейса DOCXдодыр требуется компонент Microsoft Edge WebView2 Runtime.' + #13#10 + #13#10 +
              'В вашей системе компонент не найден. Открыть страницу загрузки Microsoft WebView2?',
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      ShellExec('open', 'https://go.microsoft.com/fwlink/p/?LinkId=2124703', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
    end;
  end;
end;
