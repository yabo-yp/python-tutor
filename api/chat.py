# -*- coding: utf-8 -*-
"""《Python开发》智能学伴 —— Vercel Serverless 聊天接口
环境变量:
  LLM_KEY    大模型 API Key（必填，否则走内置示例回答）
  LLM_URL    兼容 OpenAI 的 chat/completions 地址，默认智谱
  LLM_MODEL  模型名，默认 glm-4-flash
"""
import json
import os
import re


import requests

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE, "knowledge.json"), encoding="utf-8") as f:
    KB = json.load(f)

LLM_URL = os.environ.get("LLM_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
LLM_MODEL = os.environ.get("LLM_MODEL", "glm-4-flash")
LLM_KEY = os.environ.get("LLM_KEY", "")

PERSONA = """你是《Python开发》课程的智能学伴，名叫「小P」。服务对象是在读大学生。

【核心原则：启发，不代写】
- 学生发来整道题、实验报告或要求"帮我写完/给我代码交作业"时，不要直接给出完整可提交的答案。
- 正确做法：① 用一两句话复述你理解的需求；② 指出他最可能卡住的点；③ 给出思路、关键函数或不超过 5 行的示意片段；④ 最后反问一个具体问题（例如"你打算用什么结构存这些数据？"）。
- 学生只问某个知识点、报错或语法时，可以完整讲清楚，并给最小可运行示例。
- 学生说"还是不懂"时，换个比喻再讲一遍，不要直接甩答案。

【回答要求】
- 优先依据下面提供的【课程知识库片段】作答；用到了哪一条，就在回答末尾标注「📚 参考：<条目名>」。
- 知识库没有覆盖的内容，用你自己的知识回答，但开头说明"这条不在课程知识库里"。
- 讲报错时先说"这句话的意思是…"，再说"常见原因…"，最后说"你可以这样排查…"。
- 回答用中文，简洁、口语化，多用短段落和代码块，不写长篇大论。
- 每次回答结尾可以追加一个追问建议，引导学生继续思考。

【课程知识库】
{kb}
"""

HOMEWORK_WORDS = ["帮我写完", "帮我做", "代写", "直接给我答案", "给我完整代码", "交作业", "写一份报告", "写出全部"]


def retrieve(question, top=2):
    """极简检索：按关键词重叠度给知识条目打分"""
    q = set(re.findall(r"[\u4e00-\u9fa5]{2,}|[a-zA-Z_][a-zA-Z0-9_]*", question.lower()))
    scored = []
    for topic, tips in KB.items():
        text = topic + "".join(tips)
        tokens = set(re.findall(r"[\u4e00-\u9fa5]{2,}|[a-zA-Z_][a-zA-Z0-9_]*", text.lower()))
        score = len(q & tokens)
        if score:
            scored.append((score, topic, tips))
    scored.sort(reverse=True)
    return scored[:top]


def demo_answer(question):
    """没配 LLM_KEY 时的内置示例回答，保证页面可用"""
    hits = retrieve(question)
    if not hits:
        return ("（示例回答，未配置大模型 Key）我目前只装载了课程知识库的 8 个主题，没匹配到你的问题。"
                "可以先试试问我：中文乱码怎么办、函数默认参数有什么坑、try-except 怎么写。")
    _, topic, tips = hits[0]
    body = "\n".join(f"- {t}" for t in tips)
    tail = ("你卡在哪一步？把报错信息或你写的代码贴过来，我帮你看。" if question == "帮我写完作业" else
           "你觉得哪一条和你遇到的情况最像？")
    return f"（示例回答，未配置大模型 Key）匹配到课程主题「{topic}」：\n{body}\n\n{tail}\n\n📚 参考：{topic}"


def call_llm(messages):
    kb_text = "\n".join(f"【{t}】\n" + "\n".join("  · " + x for x in tips) for t, tips in KB.items())
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "system", "content": PERSONA.format(kb=kb_text)}] + messages,
        "temperature": 0.6,
        "stream": True,
    }
    r = requests.post(LLM_URL, headers={"Authorization": "Bearer " + LLM_KEY,
                                        "Content-Type": "application/json"},
                      json=payload, stream=True, timeout=25)
    r.raise_for_status()
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            delta = json.loads(data)["choices"][0]["delta"].get("content") or ""
        except Exception:
            continue
        if delta:
            yield delta


def packet(event, payload):
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = (environ.get("PATH_INFO") or "/").rstrip("/") or "/"
    if path not in ("/", "/api/health", "/api/chat"):
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"Not Found"]
    if method == "GET":
        body = json.dumps({"ok": True, "knowledge": len(KB), "llm": bool(LLM_KEY)}, ensure_ascii=False).encode()
        start_response("200 OK", [("Content-Type", "application/json; charset=utf-8"),
                                  ("Content-Length", str(len(body)))])
        return [body]
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        data = json.loads(environ["wsgi.input"].read(length) or b"{}")
        messages = [m for m in data.get("messages", [])
                    if m.get("role") in ("user", "assistant") and str(m.get("content", "")).strip()]
        messages = messages[-12:]
        if not messages:
            raise ValueError("empty")
        question = str(messages[-1].get("content", ""))
    except Exception:
        start_response("400 Bad Request", [("Content-Type", "application/json")])
        return [b'{"error":"bad request"}']

    start_response("200 OK", [("Content-Type", "text/event-stream; charset=utf-8"),
                              ("Cache-Control", "no-store"),
                              ("X-Accel-Buffering", "no"),
                              ("Content-Encoding", "identity")])
    def gen():
        try:
            if not LLM_KEY:
                yield packet("token", {"t": demo_answer(question)})
                yield packet("done", {"demo": True})
                return
            for piece in call_llm(messages):
                yield packet("token", {"t": piece})
            yield packet("done", {"demo": False})
        except Exception as e:
            yield packet("error", {"msg": f"模型调用失败：{type(e).__name__}"})
    return gen()

# Vercel Python Runtime 需要名为 handler 的 WSGI 对象
handler = app
