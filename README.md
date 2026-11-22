# RoomAI

RoomAI 是一个面向小团队的立项讨论聊天室。伙伴通过邀请链接或 6 位邀请码进入房间，围绕一个主题实时讨论；RoomAI 可以生成讨论开场、准备问题，并在成员召唤时梳理讨论内容。

当前按 [DESIGN.md](./DESIGN.md) 推进 M1：FastAPI + WebSocket 聊天后端、SQLite、React + Vite + TypeScript + Tailwind 前端。界面优先适配手机，采用轻量的对话气泡、侧栏和内嵌讨论信息卡片。

## 本地运行

### 1. 配置模型

复制 `.env.example` 为项目根目录下的 `.env`，填入兼容 OpenAI Chat Completions 接口的密钥。默认配置使用 `https://aiping.cn/api/v1` 和 `GLM-5.3-Flash`。`.env` 只保存在本地，不要提交到 Git。

### 2. 启动后端

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

后端 API 文档地址：<http://localhost:8000/docs>。

### 3. 启动前端

另开一个终端：

```powershell
cd frontend
npm install
npm run dev
```

打开 <http://localhost:5173> 创建房间。创建者可复制邀请链接分享给伙伴；加入者也可在首页输入邀请码。

## M1 已实现

- 创建房间、生成 6 位邀请码、通过邀请码或链接加入；昵称在房间内唯一。
- SQLite 持久化消息和访客；WebSocket 广播消息、在线成员、输入状态和阶段变化。
- 图片附件（JPG / PNG / WebP / GIF，单张不超过 5MB）、引用回复、两分钟内撤回。
- `@` 成员提醒、未读提醒跳转、房主切换发散 / 收敛阶段。
- `/总结`、`/评估`、`/投票`、`/画图`、`/挑战`、`/待办`、`/结束`、`/帮助` 和 `@RoomAI` 对话入口。
- 建房时生成开场消息与准备问题；模型调用失败或未配置密钥时使用本地开场文案。

自动闲置触发、结构化想法池与评估、投票卡片、滚动总结版本、纪要导出等功能按设计文档的 M2–M4 继续实现。部署暂不纳入当前阶段。
