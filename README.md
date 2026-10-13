# Telegram 防骚扰双向聊天机器人（精简版）

Fork 自 [Hamster-Prime/Telegram_Anti-harassment_two-way_chatbot](https://github.com/Hamster-Prime/Telegram_Anti-harassment_two-way_chatbot)，删掉了 AI 审核、AI 自动回复、内容审查豁免、网络测试（Ping / NextTrace）和 RSS 订阅，只保留双向聊天的核心功能。

## 功能

- **话题式双向聊天**：每个用户在管理群组里对应一个话题。管理员在话题里直接发消息就能回复用户，支持文字、图片、视频、相册、文件、语音、圆形视频、位置、投票等类型，双方的编辑会同步到对方。
- **人机验证**：新用户发出第一条消息前要答一道本地题库里的常识题，答错次数过多会被临时封禁。
- **黑名单**：管理员可以拉黑或解封用户。临时封禁的用户可以答题自助解封，永久封禁的不行。
- **速率限制**：每分钟消息数超过上限会先收到警告，再犯就永久封禁。
- **用户信息卡片**：新话题里会附带用户资料，并提供封禁和直接联系按钮。

## 部署（Docker Compose）

镜像由 GitHub Actions 自动构建，推送到 `ghcr.io/k23flux/tg-antiharassment-bot:latest`（amd64 / arm64）。

```bash
mkdir tg-bot && cd tg-bot
wget https://raw.githubusercontent.com/K23Flux/Telegram_Anti-harassment_two-way_chatbot/main/docker-compose.yml
wget -O .env https://raw.githubusercontent.com/K23Flux/Telegram_Anti-harassment_two-way_chatbot/main/.env.example
nano .env            # 填 BOT_TOKEN、FORUM_GROUP_ID、ADMIN_IDS
docker compose up -d
```

更新到最新版：

```bash
docker compose pull && docker compose up -d
```

数据库保存在 `./data/bot.db`。

## 配置（.env）

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `BOT_TOKEN` | 从 [@BotFather](https://t.me/BotFather) 获取 | 必填 |
| `FORUM_GROUP_ID` | 开启了「话题」的超级群组 ID。把机器人设为群管理员后，在群里发 `/getid` 可以查到 | 必填 |
| `ADMIN_IDS` | 管理员的用户 ID，多个用逗号分隔 | 必填 |
| `VERIFICATION_ENABLED` | 是否开启人机验证 | `true` |
| `AUTO_UNBLOCK_ENABLED` | 是否允许临时封禁的用户答题自助解封 | `true` |
| `VERIFICATION_TIMEOUT` | 验证题的有效期（秒） | `300` |
| `MAX_VERIFICATION_ATTEMPTS` | 验证最多答错几次 | `3` |
| `MAX_MESSAGES_PER_MINUTE` | 每个用户每分钟最多发几条消息 | `30` |
| `MAX_WORKERS` | 消息处理队列的并发数 | `5` |
| `DATABASE_PATH` | 数据库路径 | `./data/bot.db` |

## 命令

用户：`/start`、`/getid`

管理员：

- `/panel`：管理面板（黑名单、用户列表）
- `/block [原因]`：在用户的话题里发送，永久拉黑该用户；也可以写成 `/block <用户ID> [原因]`
- `/unblock <用户ID>`：解封
- `/blacklist`：查看黑名单
- `/stats`：统计信息
- `/help`：帮助

## 开发

```bash
pip install -r requirements-dev.txt
python -m pytest -q
BOT_TOKEN=... FORUM_GROUP_ID=... ADMIN_IDS=... python bot.py
```

## 许可证

[MIT](LICENSE)
