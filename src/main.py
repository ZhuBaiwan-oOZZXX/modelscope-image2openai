import time
import uuid
import json
import asyncio
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import StreamingResponse
import aiohttp
import uvicorn
import hashlib
import urllib.parse

session = None

MODELSCOPE_API_URL = "https://api-inference.modelscope.cn/v1/images/generations"
MODELSCOPE_TASK_URL = "https://api-inference.modelscope.cn/v1/tasks"

DEFAULT_TIMEOUT = 120

SUPPORTED_MODELS = [
    "Qwen/Qwen-Image",
    "Tongyi-MAI/Z-Image-Turbo",
    "Qwen/Qwen-Image-Edit",
    "Qwen/Qwen-Image-Edit-2509",
    "Qwen/Qwen-Image-Edit-2511",
]


def generate_token(e: str, timestamp: str) -> str:
    s = hashlib.md5(e.encode("utf-8")).hexdigest()
    combined = s + "pic_edit" + timestamp
    final_hash = hashlib.md5(combined.encode("utf-8")).hexdigest()
    return final_hash[:5]


async def upload_image_to_baidu(data_url: str) -> str:
    """上传图片到百度并返回URL"""
    url = "https://image.baidu.com/aigc/pic_upload"
    headers = {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}

    timestamp = str(int(time.time() * 1000))
    token = generate_token(data_url, timestamp)

    payload = urllib.parse.urlencode(
        {
            "token": token,
            "scene": "pic_edit",
            "picInfo": data_url,
            "timestamp": timestamp,
        }
    )

    try:
        async with session.post(url, headers=headers, data=payload) as response:
            result = await response.json()
            if result.get("status") == 0:
                return result["data"]["url"]
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Image upload failed: {result.get('message', 'Unknown error')}",
                )
    except (aiohttp.ClientError, aiohttp.ContentTypeError) as e:
        raise HTTPException(
            status_code=502, detail=f"Network error during image upload: {str(e)}"
        )


async def process_base64_image(image_url: str) -> str:
    """处理base64图片，上传到百度图床并返回URL"""
    if image_url.startswith("data:image/"):
        return await upload_image_to_baidu(image_url)
    return image_url


def get_last_user_message(messages: List[Dict[str, Any]]) -> tuple[str, List[str]]:
    """提取最后一个用户消息的文本内容和图片"""
    text_content = ""
    images = []

    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                text_content = content
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            item_text = item.get("text", "")
                            if item_text:
                                text_content = item_text
                        elif item.get("type") == "image_url":
                            image_url_data = item.get("image_url", {})
                            if isinstance(image_url_data, dict):
                                image_url = image_url_data.get("url", "")
                                if image_url:
                                    images.append(image_url)
                            elif isinstance(image_url_data, str):
                                images.append(image_url_data)
            break

    return text_content, images


def build_modelscope_payload(
    model: str, prompt: str, image_url: Optional[str], data: Dict[str, Any]
) -> Dict[str, Any]:
    """构建ModelScope API请求payload"""
    payload = {"model": model, "prompt": prompt}

    if image_url:
        payload["image_url"] = image_url

    optional_params = [
        "negative_prompt",
        "size",
        "seed",
        "steps",
        "guidance",
        "loras",
    ]

    for param in optional_params:
        if param in data:
            payload[param] = data[param]

    return payload


async def call_modelscope_api(api_key: str, payload: Dict[str, Any]) -> str:
    """调用ModelScope API并返回task_id"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-ModelScope-Async-Mode": "true",
    }

    try:
        async with session.post(
            MODELSCOPE_API_URL,
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                raise HTTPException(
                    status_code=response.status,
                    detail=f"ModelScope API error: {error_text}",
                )

            result = await response.json()
            return result["task_id"]

    except HTTPException:
        raise
    except aiohttp.ClientError as e:
        raise HTTPException(status_code=502, detail=f"Network error: {str(e)}")


async def poll_task_result(
    api_key: str, task_id: str, timeout_seconds: int = 60
) -> Dict[str, Any]:
    """轮询任务状态直到完成或失败"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-ModelScope-Task-Type": "image_generation",
    }

    start_time = time.time()

    while time.time() - start_time < timeout_seconds:
        try:
            async with session.get(
                f"{MODELSCOPE_TASK_URL}/{task_id}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Task status check failed: {error_text}",
                    )

                data = await response.json()

                if data["task_status"] == "SUCCEED":
                    return data
                elif data["task_status"] == "FAILED":
                    raise HTTPException(
                        status_code=500, detail="Image generation failed"
                    )

                await asyncio.sleep(2)

        except HTTPException:
            raise
        except aiohttp.ClientError:
            await asyncio.sleep(2)

    raise HTTPException(status_code=504, detail="Task timeout")


def build_openai_response(model: str, api_result: Dict[str, Any]) -> Dict[str, Any]:
    """构建OpenAI格式的响应"""
    image_urls = []
    if "output_images" in api_result:
        image_urls = api_result["output_images"]

    content = (
        "\n".join([f"![]({url})" for url in image_urls])
        if image_urls
        else "图像生成完成，但未返回URL。"
    )

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        },
    }


def build_stream_response(model: str, api_result: Dict[str, Any]):
    """构建SSE流式响应"""
    chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    image_urls = api_result.get("output_images", [])
    content = (
        "\n".join([f"![]({url})" for url in image_urls])
        if image_urls
        else "图像生成完成，但未返回URL。"
    )

    yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"

    yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}]})}\n\n"

    yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop', 'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}]})}\n\n"

    yield "data: [DONE]\n\n"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global session
    session = aiohttp.ClientSession()
    yield
    await session.close()


app = FastAPI(lifespan=lifespan)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: str = Header(None)):
    """将OpenAI聊天完成请求转换为ModelScope图像生成请求"""

    if authorization and authorization.startswith("Bearer "):
        api_key = authorization[7:]
    else:
        raise HTTPException(status_code=401, detail="Invalid authorization format")

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in request body")

    model = data.get("model")
    if not model:
        raise HTTPException(status_code=400, detail="Model is required")

    messages = data.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="Messages are required")

    user_content, images = get_last_user_message(messages)

    if not user_content:
        raise HTTPException(
            status_code=400, detail="No text content found in the last user message"
        )

    if len(images) > 1:
        raise HTTPException(status_code=400, detail="Only 1 image is supported")

    image_url = None
    if images:
        image_url = await process_base64_image(images[0])

    payload = build_modelscope_payload(model, user_content, image_url, data)
    task_id = await call_modelscope_api(api_key, payload)
    result = await poll_task_result(api_key, task_id)

    if data.get("stream", False):
        return StreamingResponse(
            build_stream_response(model, result), media_type="text/event-stream"
        )

    return build_openai_response(model, result)


@app.get("/v1/models")
@app.get("/models")
async def list_models():
    """列出支持的模型"""
    return {
        "object": "list",
        "data": [
            {"id": model, "object": "model", "owned_by": "modelscope"}
            for model in SUPPORTED_MODELS
        ],
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
