import os
import requests
import gradio as gr

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")
TENANT = os.getenv("TENANT", "slsp")

def ask_ivesna(message: str):
    if not message.strip():
        return "", ""
    try:
        resp = requests.post(
            f"{API_BASE}/v1/chat",
            json={"tenant": TENANT, "message": message},
            timeout=60
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"❌ Error: {e}", ""

    answer = data.get("answer", "")
    citations = data.get("citations", [])
    if citations:
        cites_md = "\n".join(
            [f"[{i+1}] [{c['title']}]({c['url']})" for i, c in enumerate(citations)]
        )
    else:
        cites_md = "_No citations available_"
    return answer, cites_md

with gr.Blocks(
    title="Ivesna Chat",
    css="""
    .stretch-chat {
        height: calc(100vh - 300px) !important; /* full height minus padding/header */
    }
    .stretch-chat .wrap {
        height: 100% !important;
    }
    """
) as demo:
    gr.Markdown("## 💬 Ivesna Chat — Slovenská sporiteľňa")

    with gr.Row():
        with gr.Column(scale=3):
            chatbot = gr.Chatbot(label="Chat", elem_classes="stretch-chat")
            msg = gr.Textbox(
                label="Your question",
                placeholder="Aké účty ponúka Slovenská sporiteľňa?",
            )
            submit = gr.Button("Send", variant="primary")
        with gr.Column(scale=1):
            citations_box = gr.Markdown(label="Citations")

    def respond(history, message):
        answer, cites = ask_ivesna(message)
        history = history + [[message, answer]]
        return history, "", cites

    submit.click(
        respond,
        inputs=[chatbot, msg],
        outputs=[chatbot, msg, citations_box],
    )
    msg.submit(
        respond,
        inputs=[chatbot, msg],
        outputs=[chatbot, msg, citations_box],
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
