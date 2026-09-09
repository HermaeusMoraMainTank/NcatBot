# NapCat 重新部署脚本
# 请以管理员身份运行此脚本

Write-Host "=== NapCat 重新部署脚本 ===" -ForegroundColor Cyan
Write-Host ""

$image = "mlikiowa/napcat-docker:pr-1768-e310a57"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupPath = ".\napcat\backup\$timestamp"

# 1. 创建目录并保存部署信息
Write-Host "1. 创建目录并保存部署信息..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path ".\napcat\config", ".\napcat\QQ", $backupPath | Out-Null
docker inspect napcat 2>$null | Out-File -Encoding utf8 "$backupPath\container-inspect.json"
docker image inspect $image 2>$null | Out-File -Encoding utf8 "$backupPath\image-inspect.json"
docker logs napcat --since 24h 2>$null | Out-File -Encoding utf8 "$backupPath\napcat.log"

# 2. 拉取固定镜像
Write-Host "2. 拉取固定镜像..." -ForegroundColor Yellow
docker pull $image
if ($LASTEXITCODE -ne 0) { throw "docker pull failed" }

# 3. 停止并删除旧容器
Write-Host "3. 停止并删除旧容器..." -ForegroundColor Yellow
docker stop napcat 2>$null
docker rm napcat 2>$null

# 4. 使用原端口映射启动容器
Write-Host "4. 使用原端口映射启动容器..." -ForegroundColor Yellow
docker run -d `
  --name napcat `
  --restart always `
  -p 6099:6099 `
  -p 3001:3001 `
  -e NAPCAT_GID=0 `
  -e NAPCAT_UID=0 `
  -e WEBUI_TOKEN=napcat `
  -v "${PWD}\napcat\config:/app/napcat/config" `
  -v "${PWD}\napcat\QQ:/app/.config/QQ" `
  $image
if ($LASTEXITCODE -ne 0) { throw "docker run failed" }

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
