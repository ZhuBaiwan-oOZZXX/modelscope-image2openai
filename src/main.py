import time
import uuid
import json
import asyncio
import hashlib
import urllib.parse
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import StreamingResponse
import aiohttp
import uvicorn

session = None
MODELSCOPE_API_URL = "https://api-inference.modelscope.cn/v1/images/generations"
MODELSCOPE_TASK_URL = "https://api-inference.modelscope.cn/v1/tasks"
SUPPORTED_MODELS = [
    "Qwen/Qwen-Image",
    "Tongyi-MAI/Z-Image-Turbo",
    "Qwen/Qwen-Image-Edit",
    "Qwen/Qwen-Image-Edit-2509",
    "Qwen/Qwen-Image-Edit-2511",
]


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def generate_token(e, timestamp):
    return md5(md5(e) + "pic_edit" + timestamp)[:5]


async def upload_image_to_baidu(data_url):
    timestamp = str(int(time.time() * 1000))
    payload = urllib.parse.urlencode(
        {
            "token": generate_token(data_url, timestamp),
            "scene": "pic_edit",
            "picInfo": data_url,
            "timestamp": timestamp,
        }
    )
    async with session.post(
        "https://image.baidu.com/aigc/pic_upload",
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        data=payload,
    ) as resp:
        result = await resp.json()
        if result.get("status") == 0:
            return result["data"]["url"]
        raise HTTPException(
            400, f"Image upload failed: {result.get('message', 'Unknown')}"
        )


async def process_image(url):
    return await upload_image_to_baidu(url) if url.startswith("data:image/") else url


def get_last_user_message(messages):
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content, text, images = msg.get("content"), "", []
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            for item in content:
                if item.get("type") == "text" and item.get("text"):
                    text = item["text"]
                elif item.get("type") == "image_url":
                    url = item.get("image_url", {})
                    url = url.get("url", "") if isinstance(url, dict) else url
                    if url:
                        images.append(url)
        return text, images
    return "", []


def build_payload(model, prompt, image_url, data):
    payload = {"model": model, "prompt": prompt}
    if image_url:
        payload["image_url"] = image_url
    for p in ["negative_prompt", "size", "seed", "steps", "guidance", "loras"]:
        if p in data:
            payload[p] = data[p]
    return payload


async def call_api(api_key, payload):
    async with session.post(
        MODELSCOPE_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-ModelScope-Async-Mode": "true",
        },
        json=payload,
    ) as resp:
        if resp.status != 200:
            raise HTTPException(resp.status, f"API error: {await resp.text()}")
        return (await resp.json())["task_id"]


async def poll_task(api_key, task_id, timeout=60):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-ModelScope-Task-Type": "image_generation",
    }
    start = time.time()
    while time.time() - start < timeout:
        try:
            async with session.get(
                f"{MODELSCOPE_TASK_URL}/{task_id}", headers=headers
            ) as resp:
                if resp.status != 200:
                    raise HTTPException(
                        resp.status, f"Task check failed: {await resp.text()}"
                    )
                data = await resp.json()
                if data["task_status"] == "SUCCEED":
                    return data
                if data["task_status"] == "FAILED":
                    raise HTTPException(500, "Image generation failed")
        except aiohttp.ClientError:
            pass
        await asyncio.sleep(2)
    raise HTTPException(504, "Task timeout")


def build_response(model, result):
    urls = result.get("output_images", [])
    content = (
        "\n".join([f"![]({u})" for u in urls])
        if urls
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
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def build_stream(model, result):
    cid, created = f"chatcmpl-{uuid.uuid4().hex}", int(time.time())
    urls = result.get("output_images", [])
    content = (
        "\n".join([f"![]({u})" for u in urls])
        if urls
        else "图像生成完成，但未返回URL。"
    )
    yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"
    yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}]})}\n\n"
    yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop', 'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}]})}\n\n"
    yield "data: [DONE]\n\n"


@asynccontextmanager
async def lifespan(app):
    global session
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    yield
    await session.close()


app = FastAPI(lifespan=lifespan)


@app.post("/v1/chat/completions")
async def chat(request: Request, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid authorization")
    api_key = authorization[7:]
    try:
        data = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON")
    if not data.get("model"):
        raise HTTPException(400, "Model required")
    if not data.get("messages"):
        raise HTTPException(400, "Messages required")
    text, images = get_last_user_message(data["messages"])
    if not text:
        raise HTTPException(400, "No text content")
    if len(images) > 1:
        raise HTTPException(400, "Only 1 image supported")
    image_url = await process_image(images[0]) if images else None
    task_id = await call_api(
        api_key, build_payload(data["model"], text, image_url, data)
    )
    result = await poll_task(api_key, task_id)
    return (
        StreamingResponse(
            build_stream(data["model"], result), media_type="text/event-stream"
        )
        if data.get("stream")
        else build_response(data["model"], result)
    )


@app.get("/v1/models")
@app.get("/models")
async def models():
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "owned_by": "modelscope"}
            for m in SUPPORTED_MODELS
        ],
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
