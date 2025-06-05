
from openai import OpenAI

def test_openai():
    client = OpenAI(base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
    response = client.chat.completions.create(
        model="qwen2.5-1.5b-instruct",
        messages=[{"role": "user", "content": "Why the sky is blue?"}],
        temperature=0.0,
    )

    response = response.choices[0].message.content
    assert isinstance(response, str)

if __name__ == '__main__':
    test_openai()
    print(f'{__file__} passed')