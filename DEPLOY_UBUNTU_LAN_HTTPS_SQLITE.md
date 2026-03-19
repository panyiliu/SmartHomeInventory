# 小仓屋 部署手册（Ubuntu 24.04 / Docker / 局域网 / HTTPS / SQLite）

目标：
- Docker 运行（Gunicorn）
- Nginx 负责 HTTPS（自签证书，局域网）
- SQLite 持久化（`instance/fridge.db`）
- 更新/回滚靠 Git 拉取 + 重建镜像
- 备份/恢复靠备份 SQLite 文件

---

## 0) 你需要准备的内容
- 服务器：Ubuntu 24.04
- 项目目录：例如 `/home/ethan/docker/xiaocangwu`
- 服务器局域网 IP：例如 `192.168.50.15`
- 访问端口：HTTPS 用 `5445`（可在 `.env` 配）

---

## 1) 安装 Docker（一次性）

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
```

重新登录后确认：`docker ps`

---

## 2) 拉取项目到服务器（推荐 Git）

```bash
cd /home/ethan/docker
git clone <你的仓库地址> xiaocangwu
cd xiaocangwu
```

---

## 3) 配置 .env（必须）

```bash
cd /home/ethan/docker/xiaocangwu
cp .env.example .env
nano .env
```

至少填：

- `SECRET_KEY`：强随机字符串
- `NGINX_PORT_HTTPS=5445`
- 各类密钥（可选）

---

## 4) 生成 HTTPS 证书（自签）

```bash
cd /home/ethan/docker/xiaocangwu
mkdir -p ops/certs
export LAN_IP="192.168.50.15"  # 改成你的服务器IP

openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
  -keyout ops/certs/server.key \
  -out ops/certs/server.crt \
  -subj "/CN=${LAN_IP}" \
  -addext "subjectAltName=IP:${LAN_IP},DNS:xiaocangwu.local"
```

访问：`https://192.168.50.15:5445/`

> 自签证书：客户端会提示不受信任。你可以把 `server.crt` 安装到手机/电脑的受信任证书里消除提示。

---

## 5) 启动（首次部署）

```bash
cd /home/ethan/docker/xiaocangwu
mkdir -p instance data/backups
docker compose up -d --build
docker compose ps
docker compose logs -f --tail=200 web
```

SQLite 会在：`instance/fridge.db`

首次启动后，请先打开初始化页面创建管理员账号：

- `https://<你的域名或IP>:<端口>/setup`

---

## 6) 更新（Git）

流程（最稳）：

1) 先备份
```bash
cd /home/ethan/docker/xiaocangwu
bash ops/backup_sqlite.sh
```

2) 拉取新代码
```bash
git pull --rebase
```

3) 重建并重启
```bash
docker compose up -d --build
```

---

## 7) 回滚（最简单）

你有两种回滚：

### 7.1 回滚代码
- 切回到上一个 commit 或 tag，然后重建：
- `git checkout <commit或tag> && docker compose up -d --build`

### 7.2 回滚数据（SQLite）
- 用备份恢复：

```bash
bash ops/restore_sqlite.sh data/backups/fridge_sqlite_YYYY-MM-DD_HHMMSS.db.gz
```

---

## 8) 定期备份（建议加 cron）

```bash
crontab -e
```

每天凌晨 3 点：

```cron
0 3 * * * cd /home/ethan/docker/xiaocangwu && bash ops/backup_sqlite.sh >> data/backups/backup.log 2>&1
```

