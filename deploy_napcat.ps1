# NapCat 重新部署脚本
# 请以管理员身份运行此脚本

Write-Host "=== NapCat 重新部署脚本 ===" -ForegroundColor Cyan
Write-Host ""

# 1. 停止并删除旧容器
Write-Host "1. 停止并删除旧容器..." -ForegroundColor Yellow
docker stop napcat 2>$null
docker rm napcat 2>$null

# 2. 拉取最新镜像
Write-Host "2. 拉取最新 NapCat 镜像..." -ForegroundColor Yellow
docker pull mlikiowa/napcat-docker:latest

# 3. 创建必要的目录
Write-Host "3. 创建必要的目录..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path ".\napcat\config" | Out-Null
New-Item -ItemType Directory -Force -Path ".\napcat\data" | Out-Null

# 4. 启动新容器
Write-Host "4. 启动新的 NapCat 容器..." -ForegroundColor Yellow
docker run -d `
  --name napcat `
  --restart always `
  -p 6099:6099 `
  -p 3001:3001 `
  -e ACCOUNT=3555202423 `
  -e NAPCAT_GID=0 `
  -e NAPCAT_UID=0 `
  -v "${PWD}\napcat\config:/app/napcat/config" `
  -v "${PWD}\napcat\data:/app/napcat/.login" `
  mlikiowa/napcat-docker:latest

# 5. 等待容器启动
Write-Host "5. 等待容器启动..." -ForegroundColor Yellow
Start-Sleep -Seconds 10

# 6. 检查容器状态
Write-Host "6. 检查容器状态..." -ForegroundColor Yellow
docker ps | Select-String "napcat"

# 7. 显示日志
Write-Host "7. 显示容器日志..." -ForegroundColor Yellow
docker logs napcat --tail 20

Write-Host ""
Write-Host "=== 部署完成 ===" -ForegroundColor Green
Write-Host "WebUI 地址: http://localhost:6099" -ForegroundColor Cyan
Write-Host "WebSocket 地址: ws://localhost:3001" -ForegroundColor Cyan
Write-Host ""
Write-Host "请访问 WebUI 扫码登录，然后重启 NcatBot" -ForegroundColor Yellow
