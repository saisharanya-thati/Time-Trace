import os
from groq import Groq

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
models = client.models.list()

print("Models available on your Groq account:\n")
for m in models.data:
    print(" -", m.id)