#Requires -Version 7.0
<#
.SYNOPSIS
    Скрипт керування зашифрованим сховищем vault.bin для gemini-webapi-manager.
.DESCRIPTION
    Забезпечує створення та налаштування віртуального оточення Python,
    керування акаунтами Google (вхід через браузер, перегляд, видалення, ліміти),
    генерацію ключів шифрування та передачу vault.bin на віддалений сервер через SSH/SCP.
#>

[CmdletBinding()]
param()

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir   = $PSScriptRoot
$RepoDir     = Join-Path $ScriptDir "gemini-webapi-manager"
$VenvDir     = Join-Path $ScriptDir ".venv-gemini-vault"
$VaultDir    = Join-Path $ScriptDir "vault"
$EnvFile     = Join-Path $ScriptDir ".env"
$ServerCfg   = Join-Path $ScriptDir ".server-config.json"
$VaultFile   = Join-Path $VaultDir "vault.bin"
$GithubRepoUrl = "https://github.com/CatDogFishFrog/gemini-webapi-manager"

# -----------------------------------------------------------------------------
# Допоміжні функції виведення
# -----------------------------------------------------------------------------
function Write-Header {
    param([string]$Title)
    Clear-Host
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  $Title" -ForegroundColor Yellow
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Success {
    param([string]$Msg)
    Write-Host "[OK] $Msg" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Msg)
    Write-Host "[УВАГА] $Msg" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Msg)
    Write-Host "[ПОМИЛКА] $Msg" -ForegroundColor Red
}

function Pause-Menu {
    Write-Host ""
    Write-Host "Натисніть Enter для повернення в меню..." -ForegroundColor DarkGray
    [void][System.Console]::ReadLine()
}

# -----------------------------------------------------------------------------
# Робота з .env файлом
# -----------------------------------------------------------------------------
function Load-EnvFile {
    if (Test-Path $EnvFile) {
        Get-Content $EnvFile | ForEach-Object {
            $line = $_.Trim()
            if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
                $idx = $line.IndexOf("=")
                $key = $line.Substring(0, $idx).Trim()
                $val = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
                [System.Environment]::SetEnvironmentVariable($key, $val, "Process")
            }
        }
    }
}

function Set-EnvVariableInFile {
    param(
        [string]$Key,
        [string]$Value
    )
    [System.Environment]::SetEnvironmentVariable($Key, $Value, "Process")
    
    $lines = @()
    $found = $false
    if (Test-Path $EnvFile) {
        $lines = Get-Content $EnvFile
    }
    
    $newLines = @()
    foreach ($line in $lines) {
        if ($line.Trim().StartsWith("$Key=")) {
            $newLines += "$Key=`"$Value`""
            $found = $true
        } else {
            $newLines += $line
        }
    }
    if (-not $found) {
        $newLines += "$Key=`"$Value`""
    }
    
    Set-Content -Path $EnvFile -Value $newLines -Encoding utf8
}

# -----------------------------------------------------------------------------
# Встановлення та оновлення пакету
# -----------------------------------------------------------------------------
function Install-ManagerPackage {
    param([string]$PythonExe)

    if (Test-Path $RepoDir) {
        Write-Host "Встановлення пакету з локальної теки '$RepoDir' в режимі розробки (-e)..." -ForegroundColor Cyan
        & $PythonExe -m pip install -e "$RepoDir"
    } else {
        Write-Host "Локальну теку не знайдено. Встановлення пакету напряму з GitHub..." -ForegroundColor Cyan
        Write-Host "Джерело: $GithubRepoUrl" -ForegroundColor DarkGray
        $gitCmd = Get-Command "git" -ErrorAction SilentlyContinue
        if ($gitCmd) {
            Write-Host "Використовується Git..." -ForegroundColor DarkGray
            & $PythonExe -m pip install --upgrade "git+$GithubRepoUrl.git"
        } else {
            Write-Host "Git не знайдено у PATH. Встановлення через zip-архів репозиторію..." -ForegroundColor DarkGray
            & $PythonExe -m pip install --upgrade "$GithubRepoUrl/archive/refs/heads/main.zip"
        }
    }
}

function Update-Package {
    Write-Header "ОНОВЛЕННЯ ПАКЕТУ GEMINI-WEBAPI-MANAGER"
    $venvPython = Join-Path $VenvDir "Scripts\python.exe"
    Install-ManagerPackage -PythonExe $venvPython
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Оновлення завершено успішно."
    } else {
        Write-Err "Виникла помилка під час оновлення пакету."
    }
    Pause-Menu
}

# -----------------------------------------------------------------------------
# Ініціалізація та перевірка середовища Python / venv
# -----------------------------------------------------------------------------
function Ensure-Environment {
    # 1. Повідомлення про джерело проєкту
    if (Test-Path $RepoDir) {
        Write-Host "Виявлено локальну теку репозиторію: $RepoDir" -ForegroundColor DarkGray
    } else {
        Write-Warn "Локальну теку '$RepoDir' не знайдено. Робота відбуватиметься через пакет з GitHub ($GithubRepoUrl)."
    }

    # 2. Створення теки для сховища vault.bin
    if (-not (Test-Path $VaultDir)) {
        New-Item -ItemType Directory -Path $VaultDir -Force | Out-Null
    }
    $env:GEMINI_MANAGER_HOME = $VaultDir

    # 3. Перевірка Python у системі
    $pythonCmd = Get-Command "python" -ErrorAction SilentlyContinue
    if (-not $pythonCmd) {
        Write-Err "Python не знайдено в PATH. Встановіть Python 3.11+ та додайте його до PATH."
        exit 1
    }

    # 4. Перевірка / створення venv
    $venvPython = Join-Path $VenvDir "Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        Write-Host "Створення віртуального оточення venv у $VenvDir..." -ForegroundColor Cyan
        & python -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) {
            Write-Err "Не вдалося створити virtualenv."
            exit 1
        }
        Write-Host "Оновлення pip..." -ForegroundColor DarkGray
        & $venvPython -m pip install --upgrade pip | Out-Null
    }

    # 5. Перевірка наявності встановленого пакету gemini-webapi-manager
    $isInstalled = & $venvPython -c "import gemini_webapi_manager; print('OK')" 2>$null
    if ($isInstalled -ne "OK") {
        Write-Host "Встановлення gemini-webapi-manager у venv..." -ForegroundColor Cyan
        Install-ManagerPackage -PythonExe $venvPython
        if ($LASTEXITCODE -ne 0) {
            Write-Err "Помилка встановлення gemini-webapi-manager."
            exit 1
        }
        Write-Success "Пакет gemini-webapi-manager успішно встановлено."
    }

    # 6. Завантаження змінних із .env
    Load-EnvFile

    # Завжди гарантуємо, що GEMINI_MANAGER_HOME вказує на локальну теку сховища
    $env:GEMINI_MANAGER_HOME = $VaultDir

    # 7. Перевірка ключа шифрування
    Ensure-EncryptionKey $venvPython
}

function Ensure-EncryptionKey {
    param([string]$PythonExe)

    $currentKey = [System.Environment]::GetEnvironmentVariable("GEMINI_MANAGER_KEY", "Process")
    if (-not $currentKey -or $currentKey.Length -lt 16) {
        Write-Warn "Ключ шифрування GEMINI_MANAGER_KEY відсутній або коротший за 16 символів."
        Write-Host "1. Згенерувати новий ключ автоматично та зберегти у .env"
        Write-Host "2. Ввести існуючий ключ вручну"
        $choice = Read-Host "Оберіть варіант (1/2) [за замовчуванням 1]"
        if ($choice -eq "2") {
            do {
                $manualKey = Read-Host "Введіть ключ (мін. 16 символів)"
            } while ($manualKey.Length -lt 16)
            Set-EnvVariableInFile -Key "GEMINI_MANAGER_KEY" -Value $manualKey
            Set-EnvVariableInFile -Key "GEMINI_MANAGER_HOME" -Value $VaultDir
            Write-Success "Ключ збережено в .env"
        } else {
            $genKey = & $PythonExe -c "from gemini_webapi_manager.crypto import generate_key; print(generate_key())"
            $genKey = $genKey.Trim()
            Set-EnvVariableInFile -Key "GEMINI_MANAGER_KEY" -Value $genKey
            Set-EnvVariableInFile -Key "GEMINI_MANAGER_HOME" -Value $VaultDir
            Write-Success "Згенеровано новий ключ та записано в .env: $genKey"
        }
    }
}

# -----------------------------------------------------------------------------
# Операції CLI
# -----------------------------------------------------------------------------
function Invoke-ManagerCli {
    param([string[]]$CliArgs)
    $venvPython = Join-Path $VenvDir "Scripts\python.exe"
    & $venvPython -m gemini_webapi_manager @CliArgs
}

function Show-Accounts {
    Write-Header "СПИСОК АКАУНТІВ У СХОВИЩІ"
    Write-Host "Розташування сховища: $VaultFile" -ForegroundColor DarkGray
    Write-Host ""
    Invoke-ManagerCli @("list")
    Pause-Menu
}

function Add-Account {
    Write-Header "ДОДАВАННЯ АКАУНТА (LOGIN)"
    
    $slotStr = Read-Host "Введіть номер слота (1-10)"
    if (-not [int]::TryParse($slotStr, [ref]$null)) {
        Write-Err "Номер слота має бути числом."
        Pause-Menu
        return
    }
    $slot = [int]$slotStr

    Write-Host ""
    Write-Host "Оберіть браузер для авторизації:" -ForegroundColor Cyan
    Write-Host "1. auto (Спробувати системний Chrome, потім Edge, потім Chromium)"
    Write-Host "2. chrome (Рекомендовано - Google Chrome)"
    Write-Host "3. msedge (Microsoft Edge)"
    Write-Host "4. chromium (Playwright Chromium)"
    $bChoice = Read-Host "Вибір [1-4, за замовчуванням 2]"
    
    $browser = "chrome"
    switch ($bChoice) {
        "1" { $browser = "auto" }
        "2" { $browser = "chrome" }
        "3" { $browser = "msedge" }
        "4" { $browser = "chromium" }
    }

    # Якщо обрано chromium або auto, перевіряємо чи потрібен Playwright Chromium
    if ($browser -eq "chromium") {
        Write-Warn "Для використання вбудованого Chromium потрібні бінарні файли Playwright (~150-200 МБ)."
        $confirm = Read-Host "Завантажити/оновити браузер Playwright зараз? [y/N]"
        if ($confirm -eq "y" -or $confirm -eq "Y") {
            Write-Host "Завантаження Playwright Chromium..." -ForegroundColor Cyan
            Invoke-ManagerCli @("install-browser")
        }
    }

    $proxy = Read-Host "Введіть URL проксі (або натисніть Enter, якщо без проксі)"
    $cliArgs = @("login", $slot, "--browser", $browser)
    if ($proxy.Trim()) {
        $cliArgs += @("--proxy", $proxy.Trim())
    }

    Write-Host ""
    Write-Host "ІНСТРУКЦІЯ:" -ForegroundColor Yellow
    Write-Host "1. Зараз відкриється вікно браузера."
    Write-Host "2. Увійдіть у свій Google акаунт та відкрийте сторінку Gemini (якщо не відкрилася автоматично)."
    Write-Host "3. Після успішного входу скрипт автоматично перехопить куки та закриє вікно."
    Write-Host "Очікування авторизації..." -ForegroundColor Cyan
    Write-Host ""

    Invoke-ManagerCli $cliArgs
    Pause-Menu
}

function Set-ActiveAccount {
    Write-Header "ЗМІНА АКТИВНОГО СЛОТА"
    Invoke-ManagerCli @("list")
    Write-Host ""
    $slotStr = Read-Host "Введіть номер слота, який зробити активним"
    if ([int]::TryParse($slotStr, [ref]$null)) {
        Invoke-ManagerCli @("use", [int]$slotStr)
    } else {
        Write-Err "Невірний номер слота."
    }
    Pause-Menu
}

function Show-Usage {
    Write-Header "ПЕРЕВІРКА ЛІМІТІВ ТА КВОТ"
    Write-Host "Отримання актуальних даних із серверів Gemini..." -ForegroundColor Cyan
    Invoke-ManagerCli @("usage")
    Pause-Menu
}

function Remove-Account {
    Write-Header "ВИДАЛЕННЯ АКАУНТА ЗІ СЛОТА"
    Invoke-ManagerCli @("list")
    Write-Host ""
    $slotStr = Read-Host "Введіть номер слота для видалення"
    if ([int]::TryParse($slotStr, [ref]$null)) {
        Invoke-ManagerCli @("remove", [int]$slotStr)
    } else {
        Write-Err "Невірний номер слота."
    }
    Pause-Menu
}

function Set-Proxy {
    Write-Header "НАЛАШТУВАННЯ ПРОКСІ ДЛЯ СЛОТА"
    Invoke-ManagerCli @("list")
    Write-Host ""
    $slotStr = Read-Host "Введіть номер слота"
    if (-not [int]::TryParse($slotStr, [ref]$null)) {
        Write-Err "Невірний номер слота."
        Pause-Menu
        return
    }
    $proxy = Read-Host "Введіть URL проксі (залиште порожнім, щоб очистити)"
    if ($proxy.Trim()) {
        Invoke-ManagerCli @("proxy", [int]$slotStr, $proxy.Trim())
    } else {
        Invoke-ManagerCli @("proxy", [int]$slotStr)
    }
    Pause-Menu
}

function Manage-Key {
    Write-Header "ГЕНЕРАЦІЯ ТА КЕРУВАННЯ КЛЮЧЕМ ШИФРУВАННЯ"
    $venvPython = Join-Path $VenvDir "Scripts\python.exe"
    $currentKey = [System.Environment]::GetEnvironmentVariable("GEMINI_MANAGER_KEY", "Process")
    Write-Host "Поточний ключ у пам'яті: $currentKey" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "1. Згенерувати новий ключ AES-256-GCM та записати в .env"
    Write-Host "2. Ввести власний ключ вручну"
    Write-Host "0. Повернутися"
    $choice = Read-Host "Вибір"
    switch ($choice) {
        "1" {
            $genKey = & $venvPython -c "from gemini_webapi_manager.crypto import generate_key; print(generate_key())"
            $genKey = $genKey.Trim()
            Set-EnvVariableInFile -Key "GEMINI_MANAGER_KEY" -Value $genKey
            Write-Success "Новий ключ збережено у $EnvFile : $genKey"
        }
        "2" {
            $manualKey = Read-Host "Введіть новий ключ (мін. 16 символів)"
            if ($manualKey.Length -ge 16) {
                Set-EnvVariableInFile -Key "GEMINI_MANAGER_KEY" -Value $manualKey
                Write-Success "Ключ збережено у $EnvFile"
            } else {
                Write-Err "Ключ занадто короткий. Потрібно мінімум 16 символів."
            }
        }
    }
    Pause-Menu
}

# -----------------------------------------------------------------------------
# Передача сховища vault.bin на сервер (SSH/SCP)
# -----------------------------------------------------------------------------
function Send-VaultToServer {
    Write-Header "ПЕРЕДАЧА VAULT.BIN НА СЕРВЕР (SSH/SCP)"

    if (-not (Test-Path $VaultFile)) {
        Write-Err "Файл сховища не знайдено за шляхом: $VaultFile"
        Write-Warn "Спершу додайте хоча б один акаунт через пункт логіну."
        Pause-Menu
        return
    }

    # Завантаження збережених налаштувань сервера
    $cfg = @{
        Host       = "myserver.example.com"
        Port       = 22
        User       = "root"
        RemotePath = "/opt/my-gemini-app/data/vault.bin"
    }

    if (Test-Path $ServerCfg) {
        try {
            $loaded = Get-Content $ServerCfg -Raw | ConvertFrom-Json
            if ($loaded.Host) { $cfg.Host = $loaded.Host }
            if ($loaded.Port) { $cfg.Port = $loaded.Port }
            if ($loaded.User) { $cfg.User = $loaded.User }
            if ($loaded.RemotePath) { $cfg.RemotePath = $loaded.RemotePath }
        } catch {
            Write-Warn "Не вдалося прочитати попередній файл конфігурації сервера."
        }
    }

    Write-Host "Введіть параметри підключення до сервера (натисніть Enter, щоб залишити поточне значення):" -ForegroundColor Cyan
    Write-Host ""

    $inHost = Read-Host "Адреса сервера / IP [$($cfg.Host)]"
    if ($inHost.Trim()) { $cfg.Host = $inHost.Trim() }

    $inPort = Read-Host "SSH Порт [$($cfg.Port)]"
    if ($inPort.Trim() -and [int]::TryParse($inPort.Trim(), [ref]$null)) { $cfg.Port = [int]$inPort.Trim() }

    $inUser = Read-Host "Користувач SSH [$($cfg.User)]"
    if ($inUser.Trim()) { $cfg.User = $inUser.Trim() }

    $inPath = Read-Host "Цільовий шлях на сервері [$($cfg.RemotePath)]"
    if ($inPath.Trim()) { $cfg.RemotePath = $inPath.Trim() }

    # Збереження налаштувань на майбутнє
    $cfg | ConvertTo-Json | Set-Content -Path $ServerCfg -Encoding utf8

    Write-Host ""
    Write-Host "Відправка $VaultFile -> $($cfg.User)@$($cfg.Host):$($cfg.RemotePath) (порт $($cfg.Port))..." -ForegroundColor Cyan

    $scpCmd = Get-Command "scp" -ErrorAction SilentlyContinue
    if (-not $scpCmd) {
        Write-Err "Команду 'scp' не знайдено в системі. Переконайтеся, що OpenSSH Client встановлено у Windows."
        Pause-Menu
        return
    }

    & scp -P $($cfg.Port) "$VaultFile" "$($cfg.User)@$($cfg.Host):$($cfg.RemotePath)"
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Файл vault.bin успішно передано на сервер!"
        Write-Host "Пам'ятайте: на сервері у Docker повинен бути вказаний той самий GEMINI_MANAGER_KEY:" -ForegroundColor Yellow
        Write-Host "  $($env:GEMINI_MANAGER_KEY)" -ForegroundColor Green
    } else {
        Write-Err "Помилка передачі файлу через SCP (код завершення: $LASTEXITCODE)."
    }
    Pause-Menu
}

function Open-VaultFolder {
    Write-Header "ВІДКРИТТЯ ТЕКИ СХОВИЩА"
    Write-Host "Шлях: $VaultDir" -ForegroundColor Cyan
    if (Test-Path $VaultDir) {
        Start-Process explorer.exe -ArgumentList $VaultDir
    } else {
        Write-Warn "Теку ще не створено."
    }
}

# -----------------------------------------------------------------------------
# Головний цикл меню
# -----------------------------------------------------------------------------
Ensure-Environment

while ($true) {
    Write-Header "КЕРУВАННЯ СХОВИЩЕМ GEMINI-WEBAPI-MANAGER"
    Write-Host "Сховище: " -NoNewline; Write-Host "$VaultDir" -ForegroundColor Cyan
    Write-Host "Джерело: " -NoNewline
    if (Test-Path $RepoDir) {
        Write-Host "Локальна тека ($RepoDir)" -ForegroundColor DarkCyan
    } else {
        Write-Host "GitHub ($GithubRepoUrl)" -ForegroundColor DarkCyan
    }
    Write-Host "Ключ:    " -NoNewline
    $curKey = $env:GEMINI_MANAGER_KEY
    if ($curKey) {
        $masked = $curKey.Substring(0, [Math]::Min(4, $curKey.Length)) + "..." + $curKey.Substring([Math]::Max(0, $curKey.Length - 4))
        Write-Host "$masked (активний)" -ForegroundColor Green
    } else {
        Write-Host "НЕ ЗАДАНО" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "1. Список акаунтів (List)"
    Write-Host "2. Додати / залогінити акаунт у слот (Login)"
    Write-Host "3. Обрати активний акаунт (Use)"
    Write-Host "4. Перевірити ліміти використання (Usage)"
    Write-Host "5. Видалити акаунт зі слота (Remove)"
    Write-Host "6. Налаштувати проксі для слота (Proxy)"
    Write-Host "7. Згенерувати або змінити ключ шифрування (.env)"
    Write-Host "8. Перекинути vault.bin на сервер (SSH / SCP)"
    Write-Host "9. Відкрити теку сховища у Провіднику"
    Write-Host "10. Оновити пакет gemini-webapi-manager"
    Write-Host "0. Вихід"
    Write-Host ""

    $choice = Read-Host "Оберіть дію [0-10]"
    switch ($choice) {
        "1" { Show-Accounts }
        "2" { Add-Account }
        "3" { Set-ActiveAccount }
        "4" { Show-Usage }
        "5" { Remove-Account }
        "6" { Set-Proxy }
        "7" { Manage-Key }
        "8" { Send-VaultToServer }
        "9" { Open-VaultFolder }
        "10" { Update-Package }
        "0" {
            Clear-Host
            Write-Host "Роботу завершено." -ForegroundColor Cyan
            exit 0
        }
        default {
            Write-Warn "Невідомий пункт меню."
            Start-Sleep -Milliseconds 800
        }
    }
}
