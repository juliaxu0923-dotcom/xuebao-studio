# 雪宝 Studio

图片、视频生成和节点画布项目，包含用户与积分管理、邀请码、模型 / API 配置，以及即梦账号池接入。

此仓库保存当前项目代码和运行所需的画布资源。原电脑上的账号、密码、API Key、即梦登录状态、作品和上传素材不包含在仓库内，新安装需要重新配置。

## Windows 启动

需要 Python 3.11+、Node.js 22+ 和 Git。在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium --only-shell
.\.venv\Scripts\python.exe scripts/restore_canvas.py
.\.venv\Scripts\python.exe work/restart_server.py
```

访问 http://127.0.0.1:8000 。首次启动创建 `admin` 管理员，随机初始密码位于 `work/yingliu-studio/data/admin-initial-password.txt`。登录后可以在用户管理里修改密码，并在后台重新填写模型 API 配置、导入即梦账号、生成一次性邀请码。

三个服务均监听本机：Studio 为 8000，画布渲染为 8001，即梦网关为 8300。启动前应确保这些端口可用。代码上传 GitHub 不会自动产生可供朋友访问的网站；完整应用需要 Python、Node.js 和持久存储，不能直接用 GitHub Pages 运行。

## 当前功能

- 注册需要一次性邀请码；后台可管理用户、密码与积分。
- 独立图片 / 视频生成页，以及可编辑、重新生成的节点画布。
- 参考图片、视频、音频上传、预览、移除和画布引用。
- 视频生成记录可预览、选用、下载与删除。
- 复制节点保留内容和参数，并新增“原节点 → 副本”连线。
- 模型名称显示官网名称，模型可启停和删除。
- 即梦模型提供 480p、720p、1080p；时长根据所选模型校验，实际可用能力由上游账号决定。
- 即梦账号池、异常消息与已读状态；每账号并发可填写大于 8 的正整数，总并发单独控制。
- API 密钥与账号会话加密保存；任务失败按项目计费逻辑退款。

## 目录

```text
work/yingliu-studio/          Studio 后端、网页、画布适配代码
work/research-jimeng2api/     即梦网关源码与本地修改
work/start_jimeng.py          启动即梦网关
work/restart_server.py        启动三个服务
artifacts/                   画布 0.8.9 运行资源压缩包及校验信息
scripts/restore_canvas.py    校验并恢复画布运行资源
THIRD_PARTY.md               第三方来源与版本说明
```

画布压缩包包含安装包中的 Next.js 编译产物和配套 Node 依赖，解压至 `work/infinite-inspect/resources/app/web`。适配层依赖这个版本的具体 JS 文件，替换成其他版本前需要更新适配代码。

`work/yingliu-studio/Dockerfile` 和 `compose.yaml` 来自早期仅 Studio 的版本，不能直接启动完整的三服务项目；当前以本页 Windows 启动方式为准。

## 检查

以下检查使用模拟请求或临时数据库，不会提交付费生成：

```powershell
node work/check_canvas_copy.cjs
.\.venv\Scripts\python.exe work/check_video_resolutions.py
.\.venv\Scripts\python.exe -m pip install -r work/research-jimeng2api/requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest work/research-jimeng2api/tests/test_video_resolutions.py -q
```

运行数据统一放在 `work/yingliu-studio/data/`，其中包括数据库、媒体和加密密钥。该目录已被 Git 排除；迁移现有数据时需要通过私下备份同时迁移数据库和对应密钥。

