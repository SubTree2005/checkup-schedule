from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException

from .patient_api import PatientContext, get_current_patient
from .schemas import PatientAgentChatRequest

router = APIRouter(prefix="/api/patient/agent", tags=["patient-agent"])

DEFAULT_API_URL = "https://api.chatanywhere.tech/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
SYSTEM_PROMPT = """你是“检畅 AI 助手”，服务于医院体检小程序。
回答应简洁、明确、使用中文，并优先告诉用户下一步怎么做。
你可以解释常见体检项目、检查前准备、报告指标的一般含义，也可以说明小程序页面用途；不能代替医生作出诊断、开药或治疗决定。
若用户描述胸痛、呼吸困难、意识异常、大出血等紧急情况，应建议立即联系现场医护人员或急救服务。
不要声称已经替用户完成跳转、预约、取消或修改数据；这些操作必须由小程序中的确认卡片执行。
不要主动索取身份证号、完整手机号、密码、访问令牌等敏感信息。"""


def _timeout_seconds() -> float:
    try:
        configured = float(os.getenv("CHATANYWHERE_TIMEOUT_SECONDS", "45"))
    except ValueError:
        configured = 45
    return min(90, max(5, configured))


def _post_chatanywhere(url: str, api_key: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=_timeout_seconds()) as response:
        return json.loads(response.read().decode("utf-8"))


def _assistant_content(payload: dict) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("上游 AI 服务未返回有效内容") from exc
    if not isinstance(content, str) or not content.strip():
        raise ValueError("上游 AI 服务未返回有效内容")
    return content.strip()


@router.get("/status")
def patient_agent_status(_patient: PatientContext = Depends(get_current_patient)) -> dict:
    model = os.getenv("CHATANYWHERE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    return {"configured": bool(os.getenv("CHATANYWHERE_API_KEY", "").strip()), "model": model}


@router.post("/chat")
def chat_with_patient_agent(
    payload: PatientAgentChatRequest,
    _patient: PatientContext = Depends(get_current_patient),
) -> dict:
    api_key = os.getenv("CHATANYWHERE_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="AI 服务尚未配置，请联系管理员")

    api_url = os.getenv("CHATANYWHERE_API_URL", DEFAULT_API_URL).strip()
    if not api_url.startswith("https://"):
        raise HTTPException(status_code=503, detail="AI 服务地址配置无效")
    model = os.getenv("CHATANYWHERE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    page_context = payload.currentPage.strip() or "未知页面"
    messages = [
        {"role": "system", "content": f"{SYSTEM_PROMPT}\n用户当前所在页面：{page_context}。"},
        *(message.model_dump() for message in payload.messages),
    ]
    try:
        upstream = _post_chatanywhere(
            api_url,
            api_key,
            {"model": model, "messages": messages, "stream": False, "temperature": 0.3},
        )
        reply = _assistant_content(upstream)
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"AI 服务请求失败（{exc.code}）") from exc
    except (URLError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail="AI 服务暂时无法连接") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"reply": reply, "model": model}
