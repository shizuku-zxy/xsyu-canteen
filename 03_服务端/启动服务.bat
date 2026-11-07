@echo off
chcp 65001 >nul
title 西石大 今天吃啥 - 服务端
cd /d "%~dp0.."

rem ===== 选一个真能用的 Python =====
set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PY goto :gotpy
where python >nul 2>nul
if errorlevel 1 goto :nopy
set "PY=python"
:gotpy
"%PY%" -c "import sys" >nul 2>nul
if errorlevel 1 goto :badpy
echo   Python: %PY%
goto :db

:nopy
echo.
echo  [错误] 没找到 Python。请安装 Python 3.10+ 并勾选 Add python.exe to PATH，
echo         或把本脚本开头的 PY 改成 python.exe 的完整路径。
goto :end

:badpy
echo.
echo  [错误] 找到的 Python 无法运行：%PY%
goto :end

:db
if exist "04_数据库\数据\app.db" goto :run
echo.
echo  [错误] 数据库文件缺失：04_数据库\数据\app.db
echo         最终版已内置数据库，请不要删除；如需重建请使用归档中的 build_db.py。
goto :end

:run
echo.
echo  启动服务，浏览器会自动打开...
echo.
start "" http://127.0.0.1:8100/
"%PY%" -X utf8 "03_服务端\app.py" --port 8100
echo.
echo ------------------------------------------------------------
echo  服务已退出。若上面写着"已经在本机 8100 端口运行了"，
echo  说明本来就有一个在跑，浏览器直接打开 http://127.0.0.1:8100/ 即可。
echo ------------------------------------------------------------

:end
pause
