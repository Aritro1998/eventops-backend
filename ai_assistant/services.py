from openai import OpenAI
from core.settings.base import OPENAI_API_KEY

SYSTEM_PROMPT = """
    You are an assistant for EventOps platform. 
    Answer user queries about events, bookings, and payments in a helpful and concise manner.
    Only provide information that is relevant to the user's query. If you don't know the answer, say you don't know instead of making something up.
"""

class AIAssistantService:
    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)

    def chat(self, user_prompt):

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ]

        response = self.client.chat.completions.create(
            model='gpt-4o-mini',
            messages=messages,
            temperature=0.3,
        )

        return response.choices[0].message.content.strip()
