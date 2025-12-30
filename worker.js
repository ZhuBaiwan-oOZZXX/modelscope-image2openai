// worker.js - Cloudflare Worker for ModelScope Image API

const MODELSCOPE_API_URL = "https://api-inference.modelscope.cn/v1/images/generations";
const MODELSCOPE_TASK_URL = "https://api-inference.modelscope.cn/v1/tasks";

const SUPPORTED_MODELS = [
    "Qwen/Qwen-Image",
    "Tongyi-MAI/Z-Image-Turbo",
    "Qwen/Qwen-Image-Edit",
    "Qwen/Qwen-Image-Edit-2509",
    "Qwen/Qwen-Image-Edit-2511",
];

async function md5(str) {
    const data = new TextEncoder().encode(str);
    const hash = await crypto.subtle.digest("MD5", data);
    return Array.from(new Uint8Array(hash))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
}

async function generateToken(e, timestamp) {
    const s = await md5(e);
    const finalHash = await md5(s + "pic_edit" + timestamp);
    return finalHash.slice(0, 5);
}

async function uploadImageToBaidu(dataUrl) {
    const timestamp = Date.now().toString();
    const token = await generateToken(dataUrl, timestamp);

    const response = await fetch("https://image.baidu.com/aigc/pic_upload", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
        body: new URLSearchParams({ token, scene: "pic_edit", picInfo: dataUrl, timestamp }),
    });

    const result = await response.json();
    if (result.status === 0) return result.data.url;
    throw new Error(`Image upload failed: ${result.message || "Unknown error"}`);
}

async function processBase64Image(imageUrl) {
    return imageUrl.startsWith("data:image/") ? await uploadImageToBaidu(imageUrl) : imageUrl;
}

function getLastUserMessage(messages) {
    for (let i = messages.length - 1; i >= 0; i--) {
        const msg = messages[i];
        if (msg?.role !== "user") continue;

        let textContent = "";
        const images = [];
        const content = msg.content;

        if (typeof content === "string") {
            textContent = content;
        } else if (Array.isArray(content)) {
            for (const item of content) {
                if (item?.type === "text" && item.text) textContent = item.text;
                else if (item?.type === "image_url") {
                    const url = typeof item.image_url === "string" ? item.image_url : item.image_url?.url;
                    if (url) images.push(url);
                }
            }
        }
        return { textContent, images };
    }
    return { textContent: "", images: [] };
}

function buildModelscopePayload(model, prompt, imageUrl, data) {
    const payload = { model, prompt };
    if (imageUrl) payload.image_url = imageUrl;
    for (const p of ["negative_prompt", "size", "seed", "steps", "guidance", "loras"]) {
        if (data[p] !== undefined) payload[p] = data[p];
    }
    return payload;
}

async function callModelscopeApi(apiKey, payload) {
    const response = await fetch(MODELSCOPE_API_URL, {
        method: "POST",
        headers: {
            Authorization: `Bearer ${apiKey}`,
            "Content-Type": "application/json",
            "X-ModelScope-Async-Mode": "true",
        },
        body: JSON.stringify(payload),
    });

    if (!response.ok) throw new Error(`ModelScope API error: ${await response.text()}`);
    return (await response.json()).task_id;
}

async function pollTaskResult(apiKey, taskId, timeoutSeconds = 60) {
    const headers = {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
        "X-ModelScope-Task-Type": "image_generation",
    };
    const startTime = Date.now();

    while (Date.now() - startTime < timeoutSeconds * 1000) {
        const response = await fetch(`${MODELSCOPE_TASK_URL}/${taskId}`, { headers });
        if (!response.ok) throw new Error(`Task check failed: ${await response.text()}`);

        const data = await response.json();
        if (data.task_status === "SUCCEED") return data;
        if (data.task_status === "FAILED") throw new Error("Image generation failed");

        await new Promise((r) => setTimeout(r, 2000));
    }
    throw new Error("Task timeout");
}

function buildOpenaiResponse(model, result) {
    const urls = result.output_images || [];
    return {
        id: `chatcmpl-${crypto.randomUUID().replace(/-/g, "")}`,
        object: "chat.completion",
        created: Math.floor(Date.now() / 1000),
        model,
        choices: [{
            index: 0,
            message: { role: "assistant", content: urls.length ? urls.map((u) => `![](${u})`).join("\n") : "图像生成完成，但未返回URL。" },
            finish_reason: "stop",
        }],
        usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
    };
}

function buildStreamResponse(model, result) {
    const id = `chatcmpl-${crypto.randomUUID().replace(/-/g, "")}`;
    const created = Math.floor(Date.now() / 1000);
    const urls = result.output_images || [];
    const content = urls.length ? urls.map((u) => `![](${u})`).join("\n") : "图像生成完成，但未返回URL。";

    return [
        { id, object: "chat.completion.chunk", created, model, choices: [{ index: 0, delta: { role: "assistant", content: "" }, finish_reason: null }] },
        { id, object: "chat.completion.chunk", created, model, choices: [{ index: 0, delta: { content }, finish_reason: null }] },
        { id, object: "chat.completion.chunk", created, model, choices: [{ index: 0, delta: {}, finish_reason: "stop", usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } }] },
    ].map((c) => `data: ${JSON.stringify(c)}\n\n`).join("") + "data: [DONE]\n\n";
}

const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
const error = (message, status) => json({ error: { message, type: "error", code: status } }, status);

async function handleChat(request) {
    const auth = request.headers.get("Authorization");
    if (!auth?.startsWith("Bearer ")) return error("Invalid authorization format", 401);

    let data;
    try { data = await request.json(); } catch { return error("Invalid JSON", 400); }

    if (!data.model) return error("Model is required", 400);
    if (!data.messages?.length) return error("Messages are required", 400);

    const { textContent, images } = getLastUserMessage(data.messages);
    if (!textContent) return error("No text content found", 400);
    if (images.length > 1) return error("Only 1 image is supported", 400);

    try {
        const imageUrl = images.length ? await processBase64Image(images[0]) : null;
        const payload = buildModelscopePayload(data.model, textContent, imageUrl, data);
        const taskId = await callModelscopeApi(auth.slice(7), payload);
        const result = await pollTaskResult(auth.slice(7), taskId);

        return data.stream
            ? new Response(buildStreamResponse(data.model, result), { headers: { "Content-Type": "text/event-stream" } })
            : json(buildOpenaiResponse(data.model, result));
    } catch (e) {
        return error(e.message, 500);
    }
}

export default {
    async fetch(request) {
        const path = new URL(request.url).pathname;
        if (request.method === "POST" && path === "/v1/chat/completions") return handleChat(request);
        if (request.method === "GET" && (path === "/v1/models" || path === "/models")) {
            return json({ object: "list", data: SUPPORTED_MODELS.map((id) => ({ id, object: "model", owned_by: "modelscope" })) });
        }
        return error("Not Found", 404);
    },
};
