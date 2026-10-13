"""Local camera signaling and a streaming proxy for older MJPEG cameras."""
import base64
import json

import httpx


async def negotiate(ip: str, sdp: str):
    if len(sdp) > 131072 or not sdp.startswith("v=0") or "m=video " not in sdp:
        raise ValueError("Некорректное предложение видеосоединения")
    offer = base64.b64encode(json.dumps({"type": "offer", "sdp": sdp}).encode()).decode()
    async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
        response = await client.post(f"http://{ip}:8000/call/webrtc_local", content=offer,
                                     headers={"Content-Type": "plain/text"})
        response.raise_for_status()
        if len(response.content) > 262144:
            raise ValueError("Слишком большой ответ камеры")
        answer = json.loads(base64.b64decode(response.text, validate=True))
    if answer.get("type") != "answer" or not isinstance(answer.get("sdp"), str) or not answer["sdp"].startswith("v=0"):
        raise ValueError("Камера вернула некорректный ответ")
    return {"type": "answer", "sdp": answer["sdp"]}


async def open_mjpeg(ip: str, path=None):
    if path:
        from .protocols import camera_path
        path=camera_path(path,ip)
    url=path if path and path.startswith('http://') else f'http://{ip}'+path if path else f'http://{ip}:8080/?action=stream'
    client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(10, read=15))
    try:
        response = await client.send(client.build_request("GET",url), stream=True)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if not content_type.lower().startswith("multipart/x-mixed-replace"):
            raise ValueError("Камера не поддерживает MJPEG")
        return client, response, content_type
    except BaseException:
        await client.aclose()
        raise
