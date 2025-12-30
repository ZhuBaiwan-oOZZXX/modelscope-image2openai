# ModelScope Image to OpenAI

一个将 ModelScope 图像生成 API 适配为 OpenAI 格式的代理服务。通过一个 python 脚本，您可以使用熟悉的 `/chat/completions` 格式来调用 ModelScope 的图像生成服务。

> [!TIP]
> - Python 脚本运行方式：安装 `requirements.txt` 中的依赖后，运行 `main.py` 即可。
> - 除了 Python 版本外，本项目还提供了 Cloudflare Worker 版本（`worker.js`），可以直接部署到 [Cloudflare Workers](https://dash.cloudflare.com)，无需自建服务器，使用更加方便。

## 支持的模型

| 模型 | 类型 |
|---|---|
| Qwen/Qwen-Image | 文生图 |
| Tongyi-MAI/Z-Image-Turbo | 文生图 |
| Qwen/Qwen-Image-Edit | 图像编辑 |
| Qwen/Qwen-Image-Edit-2509 | 图像编辑 |
| Qwen/Qwen-Image-Edit-2511 | 图像编辑 |

> 模型列表仅用于 `/v1/models` 接口展示，实际调用时可使用 ModelScope 支持的任意 AIGC 模型。

## 模型参数说明

| 参数 | 类型 | 说明 | 取值范围 |
|---|---|---|---|
| negative_prompt | string | 负向提示词 | 长度小于2000 |
| size | string | 图像分辨率（宽x高） | Qwen-Image: [64x64, 1664x1664] |
| seed | int | 随机种子 | [0, 2^31-1] |
| steps | int | 采样步数 | [1, 100] |
| guidance | float | 提示词引导系数 | [1.5, 20] |
| loras | string/dict | LoRA模型 | 多LoRA权重总和须为1.0，最多6个 |

这些参数来自魔搭官方文档：[ModelScope 文档](https://modelscope.cn/docs/model-service/API-Inference/intro)

> [!IMPORTANT]
> - `prompt`：通过 messages 中最后一条用户消息的文本内容提取
> - `image_url`：通过 messages 中的 image_url 类型消息提取（**限1张**，base64会自动上传图床转换为URL）
> - 支持流式和非流式调用，可通过设置 `"stream": true` 参数启用流式输出

## 调用示例

### 1. 文生图 (Qwen/Qwen-Image)

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_MODELSCOPE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen-Image",
    "messages": [
      {
        "role": "user",
        "content": "写实静物摄影风格：一只新鲜的苹果静静地躺在干净柔软的表面上。苹果略微偏离中心，柔和的背光突显其自然的光泽和微妙的色彩渐变——深红色与浅金色交相辉映。细微的瑕疵、露珠和一些高光点缀其间，更显栩栩如生。浅景深柔和地模糊了中性的背景，将观众的注意力完全吸引到苹果身上。超精细的 8K 分辨率、工作室灯光和逼真的渲染，凸显了苹果的纹理和形态。"
      }
    ],
    "steps": 30,
    "negative_prompt": "低质量, 模糊"
  }'
```

![](./image/Qwen-Image.png)

### 2. 图像编辑 (Qwen/Qwen-Image-Edit-2511)

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_MODELSCOPE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen-Image-Edit",
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text": "修复并给老照片上色"
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "data:image/png;base64,..."
            }
          }
        ]
      }
    ],
    "stream": true
  }'
```

![](./image/Qwen-Image-Edit-2511.png)

**响应示例：**
```json
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "Qwen/Qwen-Image-Edit-2511",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "![](https://muse-ai.oss-cn-hangzhou.aliyuncs.com/img/97e18076f6744072988efa6255ca98fe.png)"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 1,
    "completion_tokens": 1,
    "total_tokens": 2
  }
}
```

## 快速开始

```bash
# 安装依赖
pip install fastapi aiohttp uvicorn

# 运行服务
python main.py
```

服务默认运行在 `http://localhost:8000`

获取 ModelScope 访问令牌：[ModelScope 控制台](https://modelscope.cn/my/myaccesstoken)

## Cherry Studio 中使用

1. 运行 `main.py`
2. 在设置中添加新渠道，填写 API 地址为 `http://localhost:8000`，API 密钥为 ModelScope 官方的访问令牌
3. 点击 `管理`，并添加模型（项目实现了模型列表接口）
4. 选择模型并发送对话/图片
5. 如需添加参数，右键 `助手` 编辑，在 `自定义参数` 中添加参数

![](./image/use_in_cherry.png)

## 贡献

欢迎提交 Issue 和 Pull Request！

## 许可证

MIT License - 详见 [LICENSE](LICENSE) 文件