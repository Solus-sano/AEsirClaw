# 安装 Docker
sudo apt-get update
sudo apt-get install -y docker.io

# 启动 Docker 服务
sudo systemctl start docker
sudo systemctl enable docker

# 将当前用户加入 docker 组（免 sudo）
sudo usermod -aG docker $USER
# 重新登录 shell 使组生效
newgrp docker

# 构建 Docker 镜像
cd /path/to/AEsirClaw
docker build -t aesirclaw-sandbox:latest -f docker/Dockerfile .