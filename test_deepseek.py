"""Manual DeepSeek connectivity probe. Not an automated test.

The executable body is guarded by ``main`` so that test discovery can import
this module without blocking on ``getpass`` or making a real network request.
Run it explicitly with ``python test_deepseek.py``.
"""
from getpass import getpass

from openai import OpenAI


def main():
    client = OpenAI(
        api_key=getpass("DeepSeek API Key: "),
        base_url="https://api.deepseek.com",
        timeout=30,
        max_retries=0,
    )

    response = client.chat.completions.create(
        model="deepseek-flash",
        messages=[
            {"role": "user", "content": "请只回复：连接测试成功"}
        ],
        max_tokens=128,
        extra_body={"thinking": {"type": "disabled"}},
    )

    print("模型：", response.model)
    print("回答：", response.choices[0].message.content)
    print("结束原因：", response.choices[0].finish_reason)

    if response.usage is not None:
        print("输入 token：", response.usage.prompt_tokens)
        print("输出 token：", response.usage.completion_tokens)
        print("总 token：", response.usage.total_tokens)
    else:
        print("未返回 token 用量，需进一步排查")


if __name__ == '__main__':
    main()
