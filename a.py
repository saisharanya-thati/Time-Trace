import os

try:
    from dotenv import load_dotenv
    loaded = load_dotenv()
    print("load_dotenv() found and ran. Found a .env file:", loaded)
except ImportError:
    print("python-dotenv is NOT installed. Run: pip install python-dotenv")
    loaded = False

key = os.environ.get("GROQ_API_KEY")

if not key:
    print("GROQ_API_KEY is NOT set in the environment at all.")
else:
    print("GROQ_API_KEY IS set.")
    print("Length:", len(key))
    print("Starts with:", key[:7])
    print("Ends with:", key[-4:])
    print("Has extra whitespace:", key != key.strip())
    print("Has quote characters in it:", '"' in key or "'" in key)