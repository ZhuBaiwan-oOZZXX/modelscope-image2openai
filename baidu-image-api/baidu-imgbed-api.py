import requests
import base64
import time
import hashlib
import urllib.parse


def generate_token(e: str, timestamp: str) -> str:
    s = hashlib.md5(e.encode("utf-8")).hexdigest()
    combined = s + "pic_edit" + timestamp
    final_hash = hashlib.md5(combined.encode("utf-8")).hexdigest()
    return final_hash[:5]


def get_mime_type(file_path):
    """根据文件扩展名获取MIME类型"""
    ext = file_path.lower().split(".")[-1]
    mime_map = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
    }
    return mime_map.get(ext, "image/jpeg")


def image_to_data_url(file_path):
    """将图片转换为 data URL 格式"""
    with open(file_path, "rb") as f:
        base64_str = base64.b64encode(f.read()).decode("utf-8")
    mime_type = get_mime_type(file_path)
    return f"data:{mime_type};base64,{base64_str}"


def upload_image(file_path):
    """上传图片到百度并返回响应JSON"""
    url = "https://image.baidu.com/aigc/pic_upload"
    headers = {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}

    timestamp = str(int(time.time() * 1000))
    data_url = image_to_data_url(file_path)
    token = generate_token(data_url, timestamp)

    payload = urllib.parse.urlencode(
        {
            "token": token,
            "scene": "pic_edit",
            "picInfo": data_url,
            "timestamp": timestamp,
        }
    )

    response = requests.post(url, headers=headers, data=payload)
    return response.json()


if __name__ == "__main__":
    result = upload_image("1.png")
    print(result)

# 成功返回：
"""
{'data': {'url': 'https://edit-upload-pic.cdn.bcebos.com/4c6a68331639acb7c7384e8e4853f978.jpeg?authorization=bce-auth-v1%2FALTAKh1mxHnNIyeO93hiasKJqq%2F2025-12-28T16%3A41%3A05Z%2F3600%2Fhost%2Ff423c50b7b944a7873dbe768facf40ba7ddb96b17172f198c14927e2246a2e82'}, 'message': 'success', 'status': 0}
"""

# 失败返回：
"""
{'message': '请您换张图片试试~', 'status': 1}
"""
