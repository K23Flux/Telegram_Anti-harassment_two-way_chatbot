# Telegram 防骚扰双向聊天机器人（精简版）

让陌生人通过机器人私聊你，而不用暴露你的 Telegram 账号。每个来聊天的人在你的管理群里对应一个话题，你在话题里直接回复就行。

Fork 自 [Hamster-Prime/Telegram_Anti-harassment_two-way_chatbot](https://github.com/Hamster-Prime/Telegram_Anti-harassment_two-way_chatbot)。这个版本只为「聊天」服务：删掉了 AI 审核、AI 自动回复、网络测试（Ping / NextTrace）和 RSS 订阅，补上了送达标记、发送失败提示、消息撤回和出错提醒。

## 功能

### 对方（私聊机器人的人）

- 直接给机器人发消息即可。文字、图片、视频、相册、文件、语音、圆形视频、贴纸、位置、联系人、投票都能转发。
- 编辑已经发出的消息，你那边的那条会同步更新。
- **人机验证**：第一次发消息前要回答一道常识选择题，题目来自内置题库，不需要任何 API。验证通过后，之前发的消息会自动补发。答错 3 次会被临时封禁。
- **自助解封**：被临时封禁的人再发消息时会收到一道解封题，答对就解封，答错转为永久封禁。永久封禁的人不能自助解封。
- **速率限制**：每分钟超过 30 条，超出的消息不转发，机器人提醒一次。不会因此封人。

### 你（管理群）

- 每个人对应一个话题，话题开头有一张信息卡片，包括头像、名字、ID、用户名，以及「封禁 / 解除封禁」和「直接联系」按钮。
- 在话题里直接发消息就是回复对方，各种类型都支持。编辑自己的消息会同步给对方。
- **送达标记**：消息送到对方后，机器人会在你的那条消息上点一个 👍。
- **发送失败提示**：对方停用或拉黑了机器人、触发频率限制等情况下，话题里会出现一条说明。
- **撤回**：回复自己发错的那条消息并发送 `/del`，对方那边的这条也会被删掉。
- **出错提醒**：机器人运行中出错时，在管理群的 General 里提醒你。同类错误 10 分钟内只提醒一次。
- 话题被关闭时会自动重新打开；话题被删掉时，会让对方重新验证并开一个新话题。

## 命令

输入框里的「/」菜单按身份显示：普通用户只能看到 `/start`，管理员能看到下面这些。

| 命令 | 在哪用 | 作用 |
| --- | --- | --- |
| `/start` | 私聊 | 开始聊天（显示欢迎语） |
| `/help` | 私聊 / 管理群 | 管理命令说明（仅管理员） |
| `/block [原因]` | 用户话题里 | 永久拉黑这个话题对应的用户 |
| `/block <用户ID> [原因]` | 任何地方 | 按 ID 永久拉黑 |
| `/unblock` | 用户话题里 | 解封这个话题对应的用户 |
| `/unblock <用户ID>` | 任何地方 | 按 ID 解封 |
| `/del` | 用户话题里 | 回复你发出的某条消息后发送，把对方那边的这条也删掉 |
| `/blacklist` | 任何地方 | 黑名单列表，每人后面有「解封」按钮 |
| `/stats` | 任何地方 | 用户总数、黑名单人数，可翻看所有用户和黑名单 |
| `/getid` | 任何地方 | 查看当前群 ID 和你的用户 ID（部署时用） |

除 `/start` 和 `/getid` 外，其他命令都只有 `ADMIN_IDS` 里的人能用。

## 部署

### 1. 准备

1. 找 [@BotFather](https://t.me/BotFather) 发 `/newbot`，拿到 **Bot Token**。
2. 新建一个群，在群设置里打开 **话题（Topics）**，它会自动变成超级群组。
3. 把机器人拉进群并设为管理员，至少给这两项权限：
   - **管理话题**：用来创建和重新打开话题
   - **删除消息**：`/del` 撤回后用来清理群里的原消息
4. 在群里发 `/getid`，记下 **群组 ID**（以 `-100` 开头）和 **你的用户 ID**。

### 2. 启动（Docker Compose）

镜像由 GitHub Actions 自动构建，支持 amd64 和 arm64：`ghcr.io/k23flux/tg-antiharassment-bot:latest`

```bash
mkdir tg-bot && cd tg-bot
wget https://raw.githubusercontent.com/K23Flux/Telegram_Anti-harassment_two-way_chatbot/main/docker-compose.yml
wget -O .env https://raw.githubusercontent.com/K23Flux/Telegram_Anti-harassment_two-way_chatbot/main/.env.example
nano .env            # 至少填 BOT_TOKEN、FORUM_GROUP_ID、ADMIN_IDS
docker compose up -d
docker compose logs -f   # 看到「Bot启动中」就好了
```

最后用管理员账号私聊机器人发一次 `/start`，管理命令菜单才会出现在你的私聊里。

### 3. 更新

```bash
docker compose pull && docker compose up -d
```

### 4. 数据与备份

所有数据都在 `./data/bot.db`（SQLite），只有三张表：用户、消息对应关系、黑名单。聊天记录本身存在 Telegram 上，不在这个文件里。

- 备份：复制 `data/bot.db` 即可。最好先 `docker compose stop`，或者用 `sqlite3 data/bot.db ".backup bot-backup.db"` 在线备份。
- 迁移到新机器：把 `data/` 和 `.env` 一起拷过去再启动。
- 如果这个文件丢了，老用户会被当成新用户，要重新验证并开新话题；旧话题和里面的记录还在，只是不再和用户关联。

### 从原版迁移

直接把 `docker-compose.yml` 里的镜像换成上面的地址，保留原来的 `data/` 和 `.env`，然后 `docker compose up -d`。第一次启动时会：

- 删掉不再使用的旧表：AI 审核记录、知识库、豁免名单，以及原版建了但从没用过的统计、设置等表；
- 用户、话题对应关系、黑名单、消息对应关系全部保留。

`.env` 里原来的 `GEMINI_*`、`OPENAI_*`、`ENABLE_AI_FILTER`、`RSS_*` 等变量会被忽略，可以删掉也可以留着。

## 配置（.env）

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `BOT_TOKEN` | 机器人 Token | 必填 |
| `FORUM_GROUP_ID` | 管理群 ID（开启了话题的超级群组） | 必填 |
| `ADMIN_IDS` | 管理员用户 ID，多个用英文逗号分隔 | 必填 |
| `VERIFICATION_ENABLED` | 新用户是否要先答验证题 | `true` |
| `AUTO_UNBLOCK_ENABLED` | 临时封禁的用户能否答题自助解封 | `true` |
| `VERIFICATION_TIMEOUT` | 验证题 / 解封题的有效期（秒） | `300` |
| `MAX_VERIFICATION_ATTEMPTS` | 验证最多答错几次，超过就临时封禁 | `3` |
| `MAX_MESSAGES_PER_MINUTE` | 每人每分钟最多转发几条 | `30` |
| `MAX_WORKERS` | 消息处理并发数，一般不用改 | `5` |
| `DATABASE_PATH` | 数据库路径（容器内） | `./data/bot.db` |

## 常见问题

**你发的消息没有出现 👍**
先看话题里有没有「发送失败」的提示。如果消息确实送到了却没有 👍，多半是群设置里限制了可用的表情回应，把 👍 加进允许列表就行。这不影响消息送达。

**`/del` 提示撤回失败**
Telegram 只允许机器人删除 48 小时内的消息；对方已经自己删掉的消息也无法再删。相册要一张一张撤回。

**对方删了自己的消息，你这边没变化**
Telegram 不会把私聊里的删除通知给机器人，所以这个方向无法同步。

**机器人重启后，有人说验证题点了没反应**
正在答的验证题和解封题只保存在内存里，重启后会失效。让对方重新发一条消息，就会收到新题。

**群里出现「机器人运行出错」**
完整的报错信息用 `docker compose logs --tail 200` 查看。

## 开发

```bash
pip install -r requirements-dev.txt
python -m pytest -q
cp .env.example .env && nano .env
python bot.py
```

目录结构：

```
bot.py                 入口
config.py              读取 .env
database/              SQLite：用户、消息对应关系、黑名单
handlers/
  user_handler.py      对方发来的消息：验证、限速、转发到话题
  admin_handler.py     你在话题里的回复、编辑、/del
  command_handler.py   /start /help /block /unblock /blacklist /stats /getid
  callback_handler.py  所有按钮
services/
  message_relay.py     双向复制消息、编辑同步、回复关系
  media_group_buffer.py  相册合并与按用户排队
  verification.py      人机验证
  challenges.py        本地题库
  blacklist.py         拉黑 / 解封 / 列表
  error_notifier.py    出错提醒
  telegram_commands.py 「/」命令菜单
```

## 许可证

[MIT](LICENSE)
