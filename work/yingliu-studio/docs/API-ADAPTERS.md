# 模型/API 后台配置与协议边界

## 当前适配层

| 模式 | 提交 | 结果 | 适用情形 |
|---|---|---|---|
| demo | 服务器本地生成演示资源 | 图片/短片占位 | 无供应商 API 时体验界面 |
| sync_json | POST application/json | 提交响应直接带媒体 URL | 常见同步图片接口 |
| async_json | POST application/json | 创建任务 ID → GET 轮询 → 读取结果 URL | 常见异步视频接口 |

### 公共占位符

- `{{model}}`：后台模型代号
- `{{prompt}}`：用户提示词
- `{{ratio}}`：画幅（例如 9:16）
- `{{duration}}`：时长数字（如果整个 JSON 字符串就是占位符，则保留 number 类型）
- `{{resolution}}`：用户选择分辨率（当前表单固定为 720p）
- `{{reference_base64}}`：已上传/生成的本地参考图的 data URI，可能为空
- `{{reference_image}}`：当参考是供应商可公开访问的远程图片 URL 时可用

占位符只处理请求 JSON 的**值**，不会执行任意脚本和表达式。

## 响应路径写法

使用简单点路径而不是完整 JSONPath：

- `data.0.url` → `{ "data": [{ "url": "https://media.example/1.png" }] }`
- `data.task_id` → `{ "data": { "task_id": "abc" } }`
- `data.status` → `{ "data": { "status": "completed" } }`
- `data.video_url` → `{ "data": { "video_url": "https://media.example/1.mp4" } }`

**不支持** `$.data[0].url` 方括号写法、复杂筛选、返回对象转换和反向代理转码。允许将结果路径写成 `$.data.0.url`。

## 要新增另一种供应商接口怎么办？

在 `backend/adapters.py` 中新增 Provider Adapter，后端统一输出：

```python
result_url = await invoke_provider(model, job, reference_path, reference_url, set_provider_id)
```

其中 `model` 是数据库模型配置、`job` 是用户任务、`set_provider_id()` 用于存储异步上游任务 ID。需处理鉴权、签名、参数映射、错误码、异步轮询/回调、结果解析、取消和重试。

实现后可在 `ModelIn.protocol` 添加新的枚举协议，并在 `validate_model` 里检查配置的合法性。建议以**协议类型**组织代码（OpenAI 图片同步、任务轮询通用、某厂商特殊鉴权），不要每新增一个模型就复制粘贴整个接口。

## 安全边界

- 公网通用 API 默认要求 HTTPS，禁止明显的内网/非公网目标。此处仅为基础检查，真正安全还需部署层出站防火墙、DNS 安全、内网隔离。
- 模型 Key 加密存库，API 响应不返回明文。`data/.encryption-key` 必须同数据库一起备份。
- 管理员模型编辑 API 限制角色。用户只能选择已启用模型，不得任意指定上游地址。
- 用户上传素材只能通过自己的登录态访问。若第三方供应商需要从公网 GET 图片，应使用对象存储的临时签名 URL，不应该直接将本机接口设置为永久公开。
