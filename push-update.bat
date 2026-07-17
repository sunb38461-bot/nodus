@echo off
chcp 65001 >nul
echo ========================================
echo   Nodus - 一键更新并推送到 GitHub
echo ========================================
echo.

cd /d "%~dp0"

echo [1/3] 添加所有文件...
git add .

echo [2/3] 提交更改...
set /p message=请输入提交信息 (直接回车使用默认): 
if "%message%"=="" set message=更新代码
git commit -m "%message%"

echo [3/3] 推送到 GitHub...
git push origin main

echo.
echo ========================================
echo   ✅ 更新完成！
echo ========================================
pause
